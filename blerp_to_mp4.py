#!/usr/bin/env python3
"""
blerp_to_mp4.py
================
Bir Blerp soundbite'ından animasyonlu görseli (WebP) + sesi (MP3) indirir ve
FFmpeg ile birleştirip MP4 üretir. İki mod:

  • Tek mod:   bir soundbite URL'si verilir.
  • Toplu mod: --user <kullanıcı> ya da /u/<kullanıcı> profil URL'si verilir;
               kullanıcının TÜM blerp'leri indirilir (var olanlar atlanır).

Tek-blerp pipeline'ı:
    URL -> sayfayı indir, __NEXT_DATA__ (Apollo state) JSON'unu çıkar
        -> Bite nesnesinden audio.mp3.url ve image.original.url'i çöz
        -> medyayı indir
        -> animasyonlu WebP'yi Pillow ile PNG karelere ayır (gerçek kare sürelerini
           WebP'nin ham ANMF chunk'larından okuyarak)
        -> FFmpeg concat demuxer ile sessiz animasyon videosu kur
        -> sesi sync politikasına göre videoyla birleştir -> out.mp4

Toplu mod, GraphQL API'sinden (api.blerp.com/graphql, auth gerekmez) blerp'leri
sayfalama ile listeler; yanıt ses+görsel URL'lerini de içerdiği için her blerp
için ayrıca sayfa indirmeye gerek kalmaz.

Bağımlılıklar:  Pillow  (pip install Pillow)   +   ffmpeg (PATH'te)
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from PIL import Image
except ImportError:
    sys.exit("HATA: Pillow gerekli.  Kur: pip install Pillow")

# Windows konsolu (cp1252) Türkçe karakterlerde çökmesin diye çıktıyı UTF-8'e al.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

# CDN ve site, varsayılan urllib UA'sını 403'lüyor; gerçek bir tarayıcı UA'sı şart.
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
OBJECTID_RE = re.compile(r"[0-9a-fA-F]{24}")
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
# Profil URL'si: https://blerp.com/u/<kullanıcı>
USER_URL_RE = re.compile(r"/u/([A-Za-z0-9_.\-]+)")
GRAPHQL_ENDPOINT = "https://api.blerp.com/graphql"

# --- İmza / sürüm (CLI, GUI ve installer bunları paylaşır) ---
__author__ = "RumpleSteelSkin"
__version__ = "1.0.0"
APP_NAME = "Blerp → MP4 İndirici"
SIGNATURE = f"By {__author__}"


class BlerpError(Exception):
    """Kurtarılabilir hata: tek modda programı bitirir, toplu modda blerp atlanır."""


# --------------------------------------------------------------------------- #
#  1. Ağ + scraping
# --------------------------------------------------------------------------- #
def http_get(url: str) -> bytes:
    """Bir URL'yi tarayıcı UA'sıyla indirir, ham bytes döndürür."""
    req = Request(url, headers={"User-Agent": UA})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except HTTPError as e:
        raise BlerpError(f"{url} -> HTTP {e.code}")
    except URLError as e:
        raise BlerpError(f"{url} indirilemedi -> {e.reason}")


def graphql(query: str, variables: dict) -> dict:
    """Blerp GraphQL API'sine POST atar, data bloğunu döndürür (auth gerekmez)."""
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = Request(
        GRAPHQL_ENDPOINT, data=body,
        headers={"User-Agent": UA, "Content-Type": "application/json",
                 "Origin": "https://blerp.com"},
    )
    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except URLError as e:  # HTTPError dahil (alt sınıf)
        raise BlerpError(f"GraphQL isteği başarısız: {e}")
    if data.get("errors"):
        raise BlerpError("GraphQL hatası: " + json.dumps(data["errors"])[:200])
    return data.get("data") or {}


def parse_bite_id(url: str) -> str:
    """URL içinden 24 karakterlik Blerp ObjectId'sini çıkarır."""
    m = OBJECTID_RE.search(url)
    if not m:
        raise BlerpError(f"URL'de geçerli bir blerp ID bulunamadı: {url}")
    return m.group(0)


@dataclass
class BiteMedia:
    bite_id: str
    title: str
    audio_url: str
    image_url: str
    audio_duration_s: float  # site metadatasından (ms -> s), 0 ise bilinmiyor


def _resolve_ref(apollo: dict, node):
    """Apollo {'__ref': 'Type:id'} işaretçisini gerçek nesneye çözer."""
    if isinstance(node, dict) and "__ref" in node:
        return apollo.get(node["__ref"], {})
    return node or {}


def fetch_bite_media(url: str) -> BiteMedia:
    """Soundbite sayfasını çekip ses/görsel URL'lerini ve başlığı çıkarır."""
    bite_id = parse_bite_id(url)
    html = http_get(url).decode("utf-8", "replace")

    m = NEXT_DATA_RE.search(html)
    if not m:
        raise BlerpError("Sayfada __NEXT_DATA__ bulunamadı (site yapısı değişmiş olabilir).")
    data = json.loads(m.group(1))

    apollo = data.get("props", {}).get("pageProps", {}).get("initialApolloState", {})
    bite = apollo.get(f"Bite:{bite_id}")
    if bite is None:  # ID eşleşmezse: ilk Bite tipli nesneyi bul
        bite = next((v for k, v in apollo.items() if k.startswith("Bite:")), None)
    if bite is None:
        raise BlerpError("Apollo state içinde Bite nesnesi yok.")

    audio = _resolve_ref(apollo, bite.get("audio"))
    image = _resolve_ref(apollo, bite.get("image"))
    audio_url = (audio.get("mp3") or {}).get("url", "")
    image_url = (image.get("original") or {}).get("url", "")

    if not audio_url:
        raise BlerpError("Bu blerp için ses (mp3) URL'si bulunamadı.")
    if not image_url:
        raise BlerpError("Bu blerp için görsel URL'si bulunamadı.")

    return BiteMedia(
        bite_id=bite_id,
        title=bite.get("title") or bite_id,
        audio_url=audio_url,
        image_url=image_url,
        audio_duration_s=(bite.get("audioDuration") or 0) / 1000.0,
    )


# --------------------------------------------------------------------------- #
#  1b. Toplu listeleme (GraphQL API — profil blerp'leri)
# --------------------------------------------------------------------------- #
# Profil grid'inin attığı sorgunun minimal hâli (tarayıcı ağ dinlemesiyle bulundu).
# soundEmotesFeaturedContentPagination, sayfa başına en çok 12 öğe döndürür ve
# pageInfo.pageCount/itemCount GÜVENİLMEZ (hep 12) — sadece hasNextPage'e bakılır.
_USER_ID_QUERY = (
    "query($u:String!){ web { userByUsername(username:$u){ _id username } } }"
)
_PROFILE_BITES_QUERY = """
query($page:Int,$perPage:Int,$streamerId:MongoID,$sortOverride:String,$purposeTypes:[String],$pageType:String){
  soundEmotes {
    soundEmotesFeaturedContentPagination(
      page:$page, perPage:$perPage, userId:$streamerId,
      sortOverride:$sortOverride, purposeTypes:$purposeTypes,
      pageType:$pageType, isDashboard:false
    ){
      pageInfo { hasNextPage }
      items {
        biteId
        bite {
          _id title audioDuration
          audio { mp3 { url } }
          image { original { url } }
        }
      }
    }
  }
}"""


def parse_username(target: str) -> str | None:
    """/u/<kullanıcı> profil URL'sinden kullanıcı adını çıkarır; yoksa None."""
    m = USER_URL_RE.search(target or "")
    return m.group(1) if m else None


def fetch_user_id(username: str) -> tuple[str, str]:
    """Kullanıcı adından (_id, gerçek kullanıcı adı) döndürür."""
    data = graphql(_USER_ID_QUERY, {"u": username})
    user = (data.get("web") or {}).get("userByUsername")
    if not user:
        raise BlerpError(f"Kullanıcı bulunamadı: {username}")
    return user["_id"], user.get("username") or username


def _edge_to_media(edge: dict) -> BiteMedia | None:
    """Bir pagination edge'ini BiteMedia'ya çevirir; medyası eksikse None."""
    bid = edge.get("biteId")
    b = edge.get("bite") or {}
    audio_url = ((b.get("audio") or {}).get("mp3") or {}).get("url", "")
    image_url = ((b.get("image") or {}).get("original") or {}).get("url", "")
    if not bid or not audio_url or not image_url:
        return None
    return BiteMedia(
        bite_id=bid,
        title=b.get("title") or bid,
        audio_url=audio_url,
        image_url=image_url,
        audio_duration_s=(b.get("audioDuration") or 0) / 1000.0,
    )


def list_user_bites(username: str, *, max_pages: int = 1000) -> list[BiteMedia]:
    """
    Bir kullanıcının profilindeki TÜM blerp'leri sayfalama ile toplar.
    Yanıt her blerp'in ses+görsel URL'sini de içerdiği için ekstra istek gerekmez.
    max_pages: API hiç hasNextPage=false döndürmezse sonsuz döngüye karşı güvenlik.
    """
    user_id, _ = fetch_user_id(username)
    bites: list[BiteMedia] = []
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        data = graphql(_PROFILE_BITES_QUERY, {
            "page": page, "perPage": 50, "streamerId": user_id,
            "sortOverride": "LEX_DESC_REAL", "purposeTypes": ["SOUND"],
            "pageType": "PROFILE",
        })
        pg = (data.get("soundEmotes") or {}).get("soundEmotesFeaturedContentPagination")
        items = pg.get("items", []) if pg else []
        for media in (_edge_to_media(it) for it in items):
            if media and media.bite_id not in seen:
                seen.add(media.bite_id)
                bites.append(media)
        if not pg or not items or not pg["pageInfo"]["hasNextPage"]:
            break
    return bites


# --------------------------------------------------------------------------- #
#  2. WebP kare çıkarımı (Pillow + ham ANMF süre parse'ı)
# --------------------------------------------------------------------------- #
def parse_anmf_durations(webp_bytes: bytes) -> list[int]:
    """
    WebP'nin ham RIFF chunk'larından her ANMF (animasyon karesi) süresini (ms)
    okur. Pillow bu dosyalarda süreleri 0 döndürdüğü için ground truth burada.
    Animasyonlu değilse boş liste döner.
    """
    if webp_bytes[:4] != b"RIFF" or webp_bytes[8:12] != b"WEBP":
        return []
    durations, pos = [], 12
    while pos + 8 <= len(webp_bytes):
        fourcc = webp_bytes[pos : pos + 4]
        size = struct.unpack("<I", webp_bytes[pos + 4 : pos + 8])[0]
        payload = webp_bytes[pos + 8 : pos + 8 + size]
        if fourcc == b"ANMF" and len(payload) >= 16:
            # ANMF başlığında frame_duration, 12.-14. baytlarda 24-bit little-endian.
            durations.append(payload[12] | (payload[13] << 8) | (payload[14] << 16))
        pos += 8 + size + (size & 1)  # chunk'lar çift hizalı (padding baytı)
    return durations


def extract_frames(webp_path: Path, out_dir: Path) -> tuple[list[Path], list[int]]:
    """
    WebP'yi PNG karelere ayırır. (kare_yolları, kare_süreleri_ms) döner.
    Statik görsellerde tek kare + makul bir varsayılan süre üretir.
    """
    raw = webp_path.read_bytes()
    durations = parse_anmf_durations(raw)

    im = Image.open(webp_path)
    n = getattr(im, "n_frames", 1)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames: list[Path] = []
    for i in range(n):
        im.seek(i)
        fp = out_dir / f"frame_{i:05d}.png"
        im.convert("RGBA").save(fp)
        frames.append(fp)

    # Süre listesini kare sayısıyla hizala (eksikse 40ms ~ 25fps varsayalım).
    if len(durations) != n:
        durations = [d if d > 0 else 40 for d in durations]
        durations += [40] * (n - len(durations))
    durations = [d if d > 0 else 40 for d in durations[:n]]
    return frames, durations


# --------------------------------------------------------------------------- #
#  3. FFmpeg: kareler -> sessiz animasyon videosu
# --------------------------------------------------------------------------- #
def probe_duration(media_path: Path) -> float | None:
    """ffprobe ile bir medya dosyasının gerçek süresini (s) ölçer; başarısızsa None."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(media_path)],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return None


def build_animation_video(frames: list[Path], durations_ms: list[int], out_path: Path) -> float:
    """
    PNG karelerini gerçek kare süreleriyle (concat demuxer) sessiz bir h264
    MP4'e dönüştürür. Üretilen videonun saniye cinsinden süresini döndürür.
    """
    list_file = out_path.with_suffix(".concat.txt")
    lines = []
    for fp, dur in zip(frames, durations_ms):
        p = fp.resolve().as_posix()  # ffmpeg concat: ileri eğik çizgi + tırnak
        lines.append(f"file '{p}'")
        lines.append(f"duration {dur / 1000.0:.4f}")
    # concat demuxer son karenin süresini yok sayar -> son kareyi tekrar yaz.
    lines.append(f"file '{frames[-1].resolve().as_posix()}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-vsync", "vfr",                 # değişken kare süreleri korunur
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    list_file.unlink(missing_ok=True)
    return sum(durations_ms) / 1000.0


# --------------------------------------------------------------------------- #
#  4. Sync politikası  +  ses birleştirme
# --------------------------------------------------------------------------- #
@dataclass
class SyncPlan:
    """mux() bu plana göre FFmpeg argümanlarını kurar."""
    target_duration: float          # nihai videonun saniye cinsinden uzunluğu
    loop_video: bool                # animasyon hedeften kısaysa baştan döngüle
    pad_audio_with_silence: bool    # ses hedeften kısaysa sessizlikle uzat


def resolve_sync(video_duration: float, audio_duration: float) -> SyncPlan:
    """
    Politika: "audio" (ses kral) — nihai video uzunluğu = ses uzunluğu.

    - Ses bir blerp'in ana içeriğidir, GIF onu süsler; bu yüzden ses asla kesilmez.
    - GIF anlamlı ölçüde kısaysa, sesi doldurana kadar baştan döngülenir.
    - GIF daha uzunsa, ses bittiğinde (-t ile) kesilir.
    - Hedef zaten ses uzunluğu olduğundan ses hiç sessizlikle doldurulmaz.

    TOLERANCE: süreler neredeyse eşitken (ör. 5.96s vs 5.97s) gereksiz bir döngü
    artığı eklememek için küçük bir eşik kullanılır.
    """
    TOLERANCE = 0.05  # saniye
    return SyncPlan(
        target_duration=audio_duration,
        loop_video=(video_duration + TOLERANCE < audio_duration),
        pad_audio_with_silence=False,
    )


def mux(anim_video: Path, audio_path: Path, plan: SyncPlan, out_path: Path) -> None:
    """Sessiz animasyon videosu + mp3'ü, SyncPlan'a göre nihai MP4'e birleştirir."""
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if plan.loop_video:
        cmd += ["-stream_loop", "-1"]          # videoyu sonsuz döngüle (-t ile kesilir)
    cmd += ["-i", str(anim_video), "-i", str(audio_path)]
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    cmd += ["-af", "apad" if plan.pad_audio_with_silence else "anull"]
    cmd += ["-t", f"{plan.target_duration:.4f}"]   # nihai uzunluğu kes
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)


# --------------------------------------------------------------------------- #
#  5. Orkestrasyon
# --------------------------------------------------------------------------- #
def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", name).strip() or "blerp"


def process_bite(media: BiteMedia, out_path: Path, *, verbose: bool = False) -> None:
    """
    Bir BiteMedia'yı (ses+görsel URL'leri hazır) indirip MP4'e dönüştürür.
    Hem tek hem toplu modun ortak çekirdeği; ağ/ffmpeg hatalarını yukarı fırlatır.
    """
    def log(msg: str) -> None:
        if verbose:
            print(msg)

    with tempfile.TemporaryDirectory(prefix="blerp_") as td:
        tmp = Path(td)
        webp_path, mp3_path = tmp / "image.webp", tmp / "audio.mp3"

        log("[2/5] Medya indiriliyor...")
        webp_path.write_bytes(http_get(media.image_url))
        mp3_path.write_bytes(http_get(media.audio_url))

        log("[3/5] WebP kareleri çıkarılıyor...")
        frames, durations = extract_frames(webp_path, tmp / "frames")
        log(f"      {len(frames)} kare, ~{sum(durations)/1000:.2f}s animasyon")

        log("[4/5] Animasyon videosu kuruluyor...")
        anim = tmp / "anim.mp4"
        video_dur = build_animation_video(frames, durations, anim)

        # Ses uzunluğu: önce gerçek dosyadan ölç (ground truth), olmazsa metadata.
        audio_dur = probe_duration(mp3_path) or media.audio_duration_s or video_dur
        plan = resolve_sync(video_dur, audio_dur)
        log(f"      Plan: hedef={plan.target_duration:.2f}s "
            f"loop_video={plan.loop_video} pad_audio={plan.pad_audio_with_silence}")

        log("[5/5] Ses + video birleştiriliyor...")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        mux(anim, mp3_path, plan, out_path)


def run_single(url: str, out: Path | None) -> None:
    """Tek bir soundbite URL'sini indirir."""
    print(f"[1/5] Sayfa taranıyor: {url}")
    media = fetch_bite_media(url)
    print(f"      Başlık : {media.title}")
    print(f"      Ses    : {media.audio_url}")
    print(f"      Görsel : {media.image_url}")

    out = out or Path(f"{sanitize(media.title)}.mp4")
    process_bite(media, out, verbose=True)
    print(f"\n✓ Bitti -> {out.resolve()}")


def run_bulk(username: str, out_dir: Path | None, *, limit: int | None,
             delay: float, overwrite: bool) -> None:
    """Bir kullanıcının tüm blerp'lerini sırayla indirir (var olanları atlar)."""
    print(f"Kullanıcı taranıyor: {username}")
    bites = list_user_bites(username)
    if limit:
        bites = bites[:limit]

    out_dir = out_dir or Path(sanitize(username))
    out_dir.mkdir(parents=True, exist_ok=True)
    total = len(bites)
    print(f"{total} blerp bulundu -> {out_dir}/\n")

    ok = skip = fail = 0
    for i, media in enumerate(bites, 1):
        # Dosya adı blerp ID içerir: benzersizdir VE tekrar çalıştırmada kararlıdır
        # (aynı blerp -> aynı ad), bu da "var olanı atla" (resume) için şart.
        out_path = out_dir / f"{sanitize(media.title)}_{media.bite_id}.mp4"
        tag = f"[{i}/{total}]"
        if out_path.exists() and not overwrite:
            skip += 1
            print(f"{tag} • atlandı (zaten var): {out_path.name}")
            continue
        try:
            process_bite(media, out_path)
            ok += 1
            print(f"{tag} ✓ {out_path.name}")
        except (BlerpError, subprocess.CalledProcessError, OSError) as e:
            fail += 1
            print(f"{tag} ✗ HATA ({media.title[:30]}): {e}")
        time.sleep(delay)

    print(f"\nBitti: {ok} indirildi, {skip} atlandı, {fail} hata -> {out_dir.resolve()}")


def main() -> None:
    print(f"{APP_NAME}  v{__version__}  ·  {SIGNATURE}\n")
    ap = argparse.ArgumentParser(
        description="Blerp -> MP4 (gif + ses). Tek blerp ya da bir kullanıcının tüm blerp'leri.",
        epilog=SIGNATURE)
    ap.add_argument("target", nargs="?",
                    help="Soundbite URL'si VEYA /u/<kullanıcı> profil URL'si")
    ap.add_argument("--user", metavar="KULLANICI",
                    help="Bir kullanıcının TÜM blerp'lerini indir (toplu mod)")
    ap.add_argument("-o", "--out", type=Path,
                    help="Tek mod: çıktı dosyası | Toplu mod: çıktı klasörü")
    ap.add_argument("--limit", type=int, help="Toplu modda yalnızca ilk N blerp")
    ap.add_argument("--delay", type=float, default=0.3,
                    help="Toplu modda blerp'ler arası bekleme (sn, varsayılan: 0.3)")
    ap.add_argument("--overwrite", action="store_true",
                    help="Toplu modda var olan dosyaların üzerine yaz (varsayılan: atla)")
    args = ap.parse_args()

    username = args.user or parse_username(args.target or "")
    try:
        if username:
            run_bulk(username, args.out, limit=args.limit,
                     delay=args.delay, overwrite=args.overwrite)
        elif args.target:
            run_single(args.target, args.out)
        else:
            ap.error("Bir soundbite URL'si, /u/<kullanıcı> profili ya da --user verin.")
    except BlerpError as e:
        sys.exit(f"HATA: {e}")
    except KeyboardInterrupt:
        sys.exit("\nİptal edildi.")


if __name__ == "__main__":
    main()

# Blerp Downloader (blerp_to_mp4)

Bir [Blerp](https://blerp.com) soundbite URL'sinden animasyonlu görseli (WebP) ve sesi (MP3) indirip, bunları FFmpeg ile birleştirerek tek bir **MP4** dosyası üreten, tek dosyalık bir Python 3 komut satırı aracı.

Bir blerp aslında "ses + ona eşlik eden animasyonlu görsel" ikilisidir. Bu araç ikisini de tek bir paylaşılabilir, oynatılabilir videoda toplar.

## Özellikler

- Blerp soundbite sayfasını otomatik tarar; sayfanın içindeki `__NEXT_DATA__` (Apollo state) verisinden ses (MP3) ve görsel (WebP) bağlantılarını çözer.
- Animasyonlu WebP'yi PNG karelerine ayırır ve her karenin **gerçek süresini** WebP'nin ham ANMF chunk'larından okuyarak doğru zamanlamayı korur.
- Statik (animasyonsuz) görselleri de destekler; tek kareyle makul bir varsayılan süre kullanır.
- FFmpeg concat demuxer ile değişken kare süreli, sessiz bir H.264 animasyon videosu kurar, ardından sesle birleştirir.
- Akıllı senkronizasyon politikası: **ses kraldır**. Nihai videonun uzunluğu sesin uzunluğuna eşitlenir.
  - Animasyon sesten kısaysa baştan döngülenir.
  - Animasyon sesten uzunsa, ses bittiğinde kesilir.
  - Ses asla kesilmez.
- Ses uzunluğunu önce gerçek dosyadan (`ffprobe`), o olmazsa site metadatasından, o da yoksa video süresinden belirler.
- Çıktı: web oynatımı için optimize edilmiş (`+faststart`), `yuv420p` H.264 video + 192 kbps AAC ses.
- Türkçe karakterli başlıklarda Windows konsolunun çökmemesi için çıktı UTF-8'e ayarlanır; dosya adı geçersiz karakterlerden temizlenir.

## Gereksinimler

- **Python 3.8+** — tip ipuçları `from __future__ import annotations` ile etkinleştirilen PEP 604 birleşim sözdizimini (`float | None`) kullanır.
- **Pillow** `>=10.0` — animasyonlu WebP kare çıkarımı için (`pip install Pillow`).
- **ffmpeg** ve **ffprobe** — sistemde kurulu ve `PATH` üzerinde erişilebilir olmalı.

> FFmpeg/ffprobe `pip` ile gelmez; sistemde ayrıca kurulu olmalıdır. `ffprobe` bulunmazsa araç çökmek yerine site metadatasına ya da video süresine düşerek çalışmaya devam eder.

## Kurulum

1. Bu depoyu/dosyaları indirin.
2. Python bağımlılığını kurun:

   ```bash
   pip install -r requirements.txt
   ```

   `requirements.txt` içeriği:

   ```
   Pillow>=10.0
   ```

3. FFmpeg'in ve ffprobe'un kurulu ve `PATH`'te olduğundan emin olun:

   ```bash
   ffmpeg -version
   ffprobe -version
   ```

   Komutlar sürüm bilgisi yazdırmıyorsa FFmpeg'i kurup `PATH`'inize ekleyin.

## Kullanım

Temel kullanım — bir Blerp soundbite URL'si verin:

```bash
python blerp_to_mp4.py "https://blerp.com/soundbites/<blerp-id>"
```

Çıktı dosyasının yolunu/adını `-o` (veya `--out`) ile belirtin:

```bash
python blerp_to_mp4.py "https://blerp.com/soundbites/<blerp-id>" -o cikti.mp4
```

```bash
python blerp_to_mp4.py "https://blerp.com/soundbites/<blerp-id>" --out "C:\Videolar\benim_ses.mp4"
```

`-o` verilmezse çıktı, soundbite başlığından türetilen `<başlık>.mp4` dosyası olur (örn. `Komik Ses.mp4`).

### Seçenekler

| Argüman | Açıklama |
| --- | --- |
| `url` | Blerp soundbite URL'si (zorunlu, konumsal argüman) |
| `-o`, `--out` | Çıktı MP4 yolu (varsayılan: `<başlık>.mp4`) |

Yardım için:

```bash
python blerp_to_mp4.py -h
```

### Örnek çıktı

Çalışırken ilerleme 5 adımda gösterilir:

```
[1/5] Sayfa taranıyor: https://blerp.com/soundbites/<blerp-id>
      Başlık : Örnek Ses
      Ses    : https://.../audio.mp3
      Görsel : https://.../image.webp
[2/5] Medya indiriliyor...
[3/5] WebP kareleri çıkarılıyor...
      24 kare, ~3.60s animasyon
[4/5] Animasyon videosu kuruluyor...
      Plan: hedef=5.42s loop_video=True pad_audio=False
[5/5] Ses + video birleştiriliyor...

✓ Bitti -> C:\...\Örnek Ses.mp4
```

Sonuç tek bir `.mp4` dosyasıdır: süresi sesin uzunluğuna eşit, animasyonlu görüntü gerektiğinde döngülenmiş, H.264 video + AAC ses içeren, web oynatımı için `+faststart` ile optimize edilmiş bir video.

## Nasıl Çalışır

Araç tek bir doğrusal pipeline olarak çalışır (`run()` fonksiyonu, 5 adım):

```
URL
 -> [1] Sayfayı indir, __NEXT_DATA__ (Apollo state) JSON'unu çıkar
        Bite nesnesinden audio.mp3.url ve image.original.url'i çöz
 -> [2] Medyayı (WebP + MP3) geçici dizine indir
 -> [3] Animasyonlu WebP'yi Pillow ile PNG karelere ayır
        (gerçek kare sürelerini ham ANMF chunk'larından okuyarak)
 -> [4] FFmpeg concat demuxer ile sessiz bir H.264 animasyon videosu kur
 -> [5] Sesi sync politikasına göre videoyla birleştir -> out.mp4
```

1. **Sayfa taranıyor** — `parse_bite_id()` URL içinden 24 karakterlik hex Blerp `ObjectId`'sini regex (`[0-9a-fA-F]{24}`) ile çıkarır. `fetch_bite_media()` sayfayı indirir, `__NEXT_DATA__` JSON'unu söker ve `props.pageProps.initialApolloState` altındaki Apollo Client cache'inden `Bite:<id>` nesnesini bulur. Sonuçta ses URL'si, görsel URL'si ve başlık çözülür; başlık, ses ve görsel bağlantıları yazdırılır.
2. **Medya indiriliyor** — WebP görseli ve MP3 sesi, geçici bir dizine (`tempfile.TemporaryDirectory`) `image.webp` ve `audio.mp3` olarak yazılır. Tüm ara dosyalar bu dizinde kalır ve iş bittiğinde otomatik temizlenir.
3. **WebP kareleri çıkarılıyor** — `extract_frames()` görseli Pillow ile açar, her kareyi `RGBA` PNG olarak diske yazar; gerçek kare sürelerini ham WebP byte'larından okur. Kare sayısı ve yaklaşık toplam animasyon süresi yazdırılır.
4. **Animasyon videosu kuruluyor** — `build_animation_video()` bir concat liste dosyası yazar ve FFmpeg ile sessiz `anim.mp4` (libx264, yuv420p) üretir. Ardından `probe_duration()` ses süresini ölçer, `resolve_sync()` senkronizasyon planını (`SyncPlan`) kurar ve plan (`hedef`, `loop_video`, `pad_audio`) yazdırılır.
5. **Ses + video birleştiriliyor** — `mux()`, sessiz videoyu ve sesi `SyncPlan`'a göre birleştirip nihai MP4'ü üretir ve `✓ Bitti -> <çıktı yolu>` yazdırılır.

## Teknik Notlar

### `__NEXT_DATA__` Apollo state scraping

Blerp bir Next.js sitesidir. Sayfa HTML'i içinde, sunucu tarafında render edilen veriyi taşıyan bir `<script id="__NEXT_DATA__" type="application/json">...</script>` etiketi bulunur. İlgili veri `props.pageProps.initialApolloState` altındaki **Apollo Client cache**'inde tutulur. Önce `Bite:<id>` anahtarı denenir; eşleşme yoksa `Bite:` ile başlayan ilk nesneye düşülür. Apollo cache normalize edilmiş olduğu için iç içe nesneler `{"__ref": "Type:id"}` işaretçileriyle saklanır; `_resolve_ref()` bunları gerçek nesnelere çözer. Ses `audio.mp3.url`'den, görsel `image.original.url`'den okunur.

> **User-Agent spoofing:** CDN ve site, varsayılan `urllib` User-Agent'ını **403** ile reddeder. Bu yüzden tüm istekler (`http_get()`) gerçek bir Chrome/120 masaüstü tarayıcı UA başlığıyla, 30 saniyelik zaman aşımıyla yapılır.

### Animasyonlu WebP — GIF değil

Blerp görselleri GIF değil, **animasyonlu WebP**'dir. Burada iki teknik incelik vardır:

**a) FFmpeg animasyonlu WebP'yi decode edemez.** Bu yüzden kare ayrımı FFmpeg'e bırakılmaz; `extract_frames()` görseli **Pillow** ile açar, `n_frames` kadar `seek()` yapıp her kareyi `RGBA` PNG olarak diske yazar (`frame_00000.png`, `frame_00001.png`, ...).

**b) Pillow bu dosyalarda kare sürelerini güvenilir döndürmez (çoğunlukla 0 verir).** Gerçek kare süreleri (ground truth) doğrudan WebP'nin ham byte'larından okunur. `parse_anmf_durations()` RIFF/WEBP container'ını gezerek her `ANMF` (animation frame) chunk'ını bulur ve başlığın 12.–14. byte'larındaki **24-bit little-endian frame_duration** değerini (ms) çıkarır. RIFF chunk'ları çift hizalı olduğundan tek boyutlu payload'larda padding byte'ı atlanır.

Süre listesi kare sayısıyla hizalanır: eksik ya da 0 olan süreler **40 ms (~25 fps)** varsayılanıyla doldurulur. Statik (tek kareli) görsellerde de tek kare + makul bir varsayılan süre üretilir.

### FFmpeg concat demuxer ile değişken kare süresi

`build_animation_video()` bir **concat demuxer** liste dosyası (`*.concat.txt`, UTF-8) oluşturur. Her kare için `file '<yol>'` ve onun gerçek süresi `duration <saniye>` satırı yazılır (yollar FFmpeg uyumluluğu için ileri eğik çizgili ve tek tırnaklıdır). concat demuxer **son karenin `duration` değerini yok saydığı** için son kare listeye bir kez daha eklenir. Video `libx264` / `yuv420p` ile, değişken kare sürelerini korumak için `-vsync vfr` kullanılarak render edilir ve `+faststart` ile yazılır.

### Ses süresi ölçümü ve "ses kraldır" senkronizasyonu

Sitenin `audioDuration` metadatası her zaman güvenilir değildir. Bu yüzden nihai uzunluk belirlenmeden önce `probe_duration()` ile **ffprobe** kullanılarak indirilen MP3'ün **gerçek süresi** ölçülür. Ground truth bu ölçümdür; başarısız olursa sırasıyla metadata süresine, o da yoksa video süresine düşülür:

```python
audio_dur = probe_duration(mp3_path) or media.audio_duration_s or video_dur
```

`resolve_sync()` politikası **"ses kraldır"** ilkesine dayanır (`TOLERANCE = 0.05s`) ve bir `SyncPlan` döndürür:

- Nihai video uzunluğu **her zaman ses uzunluğuna** eşitlenir; video, `-t` ile ses uzunluğunda kesilir. Ses bir blerp'in ana içeriğidir, görsel onu süsler — bu yüzden ses asla kesilmez.
- Animasyon sesten anlamlı ölçüde kısaysa (`video + TOLERANCE < ses`), ses dolana kadar **video baştan döngülenir** (`mux()` içinde `-stream_loop -1`).
- Hedef zaten ses uzunluğu olduğundan ses, sessizlikle doldurulmaz (`pad_audio_with_silence` her zaman `False`'tur).

Bu politika sabittir ve değiştirilebilir bir CLI seçeneği yoktur. Aynı şekilde kodek/kalite ayarları da sabittir: video `libx264` / `yuv420p`, ses `aac` 192 kbps — codec, bit hızı veya çözünürlük için CLI kontrolü yoktur.

## Sorun Giderme

- **`HATA: Pillow gerekli.`** — `pip install Pillow` komutunu çalıştırın.
- **`ffmpeg` / `ffprobe` bulunamadı (FileNotFoundError vb.)** — FFmpeg kurulu değil veya `PATH`'te değil; `ffmpeg -version` ile doğrulayın. (FFmpeg birleştirme adımında başarısız olursa hata yakalanmadan yığın izi olarak yansır.)
- **`HATA: URL'de geçerli bir blerp ID bulunamadı`** — Verdiğiniz URL içinde 24 karakterlik blerp kimliği yok; doğrudan soundbite sayfasının tam adresini kullanın.
- **`HATA: <url> -> HTTP 403` / `HATA: <url> indirilemedi`** — CDN veya site erişimi engellemiş olabilir; bağlantınızı ve URL'yi kontrol edip tekrar deneyin.
- **`HATA: Sayfada __NEXT_DATA__ bulunamadı`** veya **`HATA: Apollo state içinde Bite nesnesi yok.`** — Blerp'in sayfa yapısı değişmiş olabilir; aracın güncellenmesi gerekebilir.
- **`HATA: Bu blerp için ses (mp3) URL'si bulunamadı.`** / **`... görsel URL'si bulunamadı.`** — Bu soundbite için ilgili medya verisi sayfada yoktu; başka bir blerp deneyin.

## Yasal Uyarı

Bu araç yalnızca kişisel ve eğitim amaçlıdır. Kullanmadan önce [Blerp](https://blerp.com)'in kullanım koşullarına ve hizmet şartlarına uyduğunuzdan emin olun. Yalnızca indirmeye ve kullanmaya hakkınız olan içeriği indirin. İndirilen ses ve görsellerin telif hakları ilgili sahiplerine aittir; bu içeriğin kullanımına ilişkin tüm sorumluluk kullanıcıya aittir.

# Blerp -> MP4 İndirici

> 🌐 [English](README.md) · **Türkçe**

Bir Blerp soundbite'ının animasyonlu görselini (WebP) ve sesini (MP3) indirip FFmpeg ile birleştirerek MP4 üretir.

## Özellikler

- **İki çalışma modu:** Tek bir soundbite indir ya da bir kullanıcının TÜM blerp'lerini toplu indir.
- **Animasyonlu WebP -> MP4:** Görsel ile sesi tek bir MP4 dosyasında birleştirir.
- **Gerçek kare süreleri:** Animasyonun her karesinin süresini WebP'nin ham ANMF chunk'larından okuyarak hızı bozmadan korur.
- **"Ses kral" senkronu:** Nihai videonun uzunluğu sesin uzunluğuna eşitlenir; animasyon kısaysa döngülenir, uzunsa kesilir, ses asla kesilmez.
- **Toplu modda resume:** Var olan dosyalar atlanır; yarıda kalan bir indirme baştan başlamadan kaldığı yerden sürer.
- **Kimlik doğrulama gerektirmez:** Toplu listeleme, Blerp'in açık GraphQL API'sini kullanır.
- **Türkçe arayüz:** Tüm çıktı ve hata mesajları Türkçedir.

## Gereksinimler

- **Python 3.8+**
- **ffmpeg** ve **ffprobe** — ikisi de PATH üzerinde erişilebilir olmalı (harici ikili dosyalar; `requirements.txt`'te yer almaz).
- **Pillow** (`Pillow>=10.0`) — animasyonlu WebP'yi karelere ayırmak için.

## Kurulum

```bash
# Python bağımlılığı
pip install -r requirements.txt
# (veya doğrudan)
pip install Pillow
```

ffmpeg/ffprobe kurulumu:

```bash
# Windows (winget)
winget install Gyan.FFmpeg

# macOS (Homebrew)
brew install ffmpeg

# Debian / Ubuntu
sudo apt install ffmpeg
```

Kurulumu doğrula:

```bash
ffmpeg -version
ffprobe -version
```

## Kullanım

### Tek mod (tek bir blerp)

```bash
# Varsayılan: ./<başlık>.mp4 olarak kaydeder
python blerp_to_mp4.py "<soundbite-url>"

# Çıktı dosyasını belirt
python blerp_to_mp4.py "<soundbite-url>" -o cikti.mp4
```

Tek mod, sürecin 5 adımını (`[1/5]`...`[5/5]`) ekrana basar.

### Toplu mod (bir kullanıcının tüm blerp'leri)

```bash
# --user ile kullanıcı adı
python blerp_to_mp4.py --user blerpusername

# ya da profil URL'si (/u/<kullanıcı>)
python blerp_to_mp4.py "https://blerp.com/u/blerpusername"

# Yalnızca ilk 10 blerp
python blerp_to_mp4.py --user blerpusername --limit 10

# Çıktı klasörünü belirt (varsayılan: ./<kullanıcı>/)
python blerp_to_mp4.py --user blerpusername -o klasor/

# Var olan dosyaların üzerine yaz (varsayılan: atla)
python blerp_to_mp4.py --user blerpusername --overwrite
```

Toplu modda dosyalar `<başlık>_<biteId>.mp4` olarak adlandırılır ve var olanlar atlanır (resume). İşlem sonunda `<n> indirildi, <n> atlandı, <n> hata` özeti basılır.

> **Not:** Hem `--user` (veya bir `/u/` profil URL'si) hem de bir soundbite URL'si birlikte verilirse toplu mod kazanır; tek-blerp URL'si yok sayılır.

### Grafik arayüz (GUI)

Yalnızca Python standart kütüphanesini kullanan (ek bağımlılık yok) basit bir Tkinter arayüzü gelir:

```bash
python blerp_gui.py
```

Tek kutuya bir soundbite URL'si **ya da** kullanıcı adı / profil URL'si yapıştırın (mod otomatik algılanır), isterseniz bir çıktı klasörü seçin ve **İndir**'e basın. Bir ilerleme çubuğu ve canlı log gösterilir; uzun toplu indirmeler çalışırken **Durdur** ile kesilebilir.

## Seçenekler

| Argüman | Açıklama |
|---|---|
| `target` (pozisyonel, opsiyonel) | Soundbite URL'si **VEYA** `/u/<kullanıcı>` profil URL'si |
| `--user KULLANICI` | Bir kullanıcının TÜM blerp'lerini indir (toplu mod) |
| `-o`, `--out` | Tek mod: çıktı dosyası \| Toplu mod: çıktı klasörü |
| `--limit N` | Toplu modda yalnızca ilk N blerp (`bites[:N]`) |
| `--delay SN` | Toplu modda blerp'ler arası bekleme (saniye, varsayılan: `0.3`) |
| `--overwrite` | Toplu modda var olan dosyaların üzerine yaz (varsayılan: atla) |

> `--limit`, `--delay` ve `--overwrite` yalnızca toplu modda etkilidir. `-o/--out`, tek modda dosya, toplu modda klasör olarak yorumlanır.

## Nasıl Çalışır

### Tek-blerp pipeline'ı

1. **[1/5] Sayfa taranır:** URL içindeki 24 karakterlik ObjectId çözülür, sayfa bir tarayıcı User-Agent'ı ile indirilir, `<script id="__NEXT_DATA__">` JSON'u çıkarılır. `props.pageProps.initialApolloState` içinden `Bite:<id>` nesnesi (yoksa ilk `Bite:` anahtarı) bulunur; `audio.mp3.url` ve `image.original.url` Apollo `__ref` işaretçileri çözülerek elde edilir.
2. **[2/5] Medya indirilir:** Görsel `image.webp`, ses `audio.mp3` olarak geçici bir klasöre yazılır.
3. **[3/5] Kareler çıkarılır:** WebP, Pillow ile PNG karelere (`frame_00000.png`...) ayrılır; her karenin gerçek süresi ham ANMF chunk'larından okunur, eksik süreler 40ms (~25fps) varsayılır.
4. **[4/5] Animasyon videosu kurulur:** Bir concat demuxer listesi yazılır (son kare iki kez eklenir, çünkü concat son sürenin değerini yok sayar) ve `ffmpeg ... -vsync vfr -c:v libx264 -pix_fmt yuv420p` ile sessiz bir h264 MP4 üretilir.
5. **[5/5] Senkron + birleştirme:** Sesin gerçek uzunluğu `ffprobe` ile ölçülür, `SyncPlan` kurulur ve `ffmpeg` ile görsel + ses son MP4'e mux edilir.

### Toplu listeleme (GraphQL)

- Önce `userByUsername` sorgusuyla kullanıcının `_id`'si bulunur (kullanıcı yoksa "Kullanıcı bulunamadı" hatası).
- `soundEmotesFeaturedContentPagination` sorgusu, kimlik doğrulama gerektirmeyen açık GraphQL endpoint'i (`https://api.blerp.com/graphql`) üzerinden sayfa sayfa çağrılır.
- Liste yanıtı her blerp'in ses (`audio.mp3.url`) ve görsel (`image.original.url`) URL'lerini de içerdiği için her blerp için ayrıca sayfa indirmeye gerek kalmaz.
- Blerp'ler **sırayla** (tek tek, paralel değil) işlenir; her blerp `process_bite` ortak çekirdeğinden geçer. Toplu mod, tek modun bastığı `[2/5]`...`[5/5]` alt adımlarını ekrana basmaz.

## Teknik Notlar

- **GIF değil, animasyonlu WebP:** Blerp görselleri animasyonlu WebP'dir. FFmpeg bu formatı güvenilir biçimde çözemediği için kareleri **Pillow** ayrıştırır, ardından FFmpeg yalnızca PNG karelerini birleştirir.
- **Ham ANMF süreleri:** Pillow bu dosyalarda kare sürelerini `0` döndürdüğünden, gerçek süreler doğrudan WebP RIFF/ANMF chunk'larından (payload'ın 12.-14. baytlarındaki 24-bit little-endian değer) okunur. Bu, animasyon hızının orijinaliyle aynı kalmasını sağlar.
- **ffprobe ile gerçek ses uzunluğu:** Senkronda kullanılacak ses uzunluğu sırasıyla şu öncelikle çözülür: önce `ffprobe` ile ölçülen gerçek değer, sonra site metadatası (`audioDuration`, ms->s), en son üretilen video süresi.
- **"Ses kral" senkronu:** Nihai uzunluk = ses uzunluğu. Animasyon, sesten anlamlı ölçüde kısaysa (`TOLERANCE = 0.05s`) baştan döngülenir; daha uzunsa ses bittiğinde `-t` ile kesilir; ses asla sessizlikle doldurulmaz.
- **GraphQL ayrıntıları (toplu):**
  - Endpoint açıktır, **auth gerektirmez**; isteklerde bir tarayıcı User-Agent'ı ve `Origin: https://blerp.com` gönderilir.
  - İstek `perPage=50` gönderse de **sunucu sayfa başına yanıtı 12 öğeyle sınırlar**; `pageInfo.pageCount`/`itemCount` güvenilmezdir (hep 12) ve kullanılmaz — döngü kontrolünde yalnızca `pageInfo.hasNextPage` güvenilirdir.
  - Sayfalama, `hasNextPage` false olunca (ya da öğe kalmayınca) durur; `hasNextPage` hiç kapanmazsa `max_pages=1000` üst sınırı sonsuz döngüyü engeller.
- **Dosya adlandırma (toplu):** `<başlık>_<biteId>.mp4`. blerp ID'sinin ada eklenmesi adları benzersiz **ve** çalıştırmalar arası kararlı kılar (aynı blerp -> aynı ad); bu da resume/atla davranışının temelidir.
- **Geçici dosyalar:** WebP, MP3, PNG kareler, ara animasyon ve concat listesi otomatik temizlenen bir `TemporaryDirectory` içinde tutulur; yalnızca nihai MP4 kalıcıdır.
- **Konsol/kodlama:** stdout/stderr UTF-8'e yeniden yapılandırılır; bu yüzden Windows konsolu (cp1252) Türkçe karakterlerde ve `•`, `✓`, `✗` gibi simgelerde çökmez.

## Sorun Giderme

- **`HATA: Pillow gerekli.`** — `pip install Pillow` çalıştırın.
- **`'ffmpeg' / 'ffprobe' bulunamadı` (FileNotFoundError) ya da mux/probe başarısız** — ffmpeg ve ffprobe'un kurulu ve PATH üzerinde olduğundan emin olun (`ffmpeg -version`, `ffprobe -version`).
- **`HTTP 403` / indirilemedi** — site/CDN varsayılan urllib User-Agent'ını engeller; betik zaten tarayıcı UA'sı gönderir. Hata sürerse ağ/erişim sorununu kontrol edin. Betikte ağ yeniden deneme/backoff yoktur; tek modda hata programı bitirir, toplu modda yalnızca o blerp atlanır.
- **`Sayfada __NEXT_DATA__ bulunamadı (site yapısı değişmiş olabilir).`** — Tek-mod scraping'i sitenin `__NEXT_DATA__`/Apollo yapısına bağlıdır; site yapısı değişmiş olabilir.
- **`Kullanıcı bulunamadı: <ad>`** — Toplu modda kullanıcı adı hatalı ya da kullanıcı yok.
- **`Bu blerp için ses/görsel URL'si bulunamadı.`** — Beklenen `audio.mp3.url`/`image.original.url` alanları bulunamadı. Toplu modda, medyası eksik öğeler sessizce listeden düşürülür.
- **`İptal edildi.`** — İşlem Ctrl+C ile durduruldu.
- **Statik / WebP olmayan görsel:** ANMF süreleri okunamazsa Pillow + 40ms varsayılan süre ile tek/çok kare yine de işlenir.

## Yasal Uyarı

Bu araç yalnızca Blerp'in hizmet şartlarına (ToS) uygun şekilde ve indirme hakkına sahip olduğunuz içerik için kullanılmalıdır. İndirilen içeriğin telif hakları ve kullanım koşulları size aittir; üçüncü taraflara ait içeriği izinsiz indirmek, dağıtmak ya da yeniden yayımlamak sizin sorumluluğunuzdadır. Toplu modda `--delay` ile istekler arasında bekleme bırakarak servise nazik davranın.

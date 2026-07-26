<p align="center">
  <img src="assets/logo.png" alt="Blerp Downloader" width="128">
</p>

<h1 align="center">Blerp -> MP4 İndirici</h1>

<p align="center"><em>By RumpleSteelSkin</em></p>

> 🌐 [English](README.md) · **Türkçe**

Bir Blerp soundbite'ının animasyonlu görselini (WebP) ve sesini (MP3) indirip FFmpeg ile birleştirerek MP4 üretir.

> ⚙️ **Tek dış gereksinim FFmpeg'dir.** Geri kalan her şey (Python, Pillow) paketlenmiş sürümde gömülüdür. FFmpeg yoksa **uygulama çökmek yerine sizi yönlendirir** — Windows'ta en hızlı çözüm:
>
> ```bash
> winget install Gyan.FFmpeg
> ```
>
> Sonra uygulamayı yeniden başlatın. (Installer da FFmpeg'i winget ile otomatik kurar.) Alternatifler için [Sorun Giderme](#sorun-giderme) bölümüne bakın.

## Özellikler

- **İki çalışma modu:** Tek bir soundbite indir ya da bir kullanıcının TÜM blerp'lerini toplu indir.
- **Animasyonlu WebP -> MP4:** Görsel ile sesi tek bir MP4 dosyasında birleştirir.
- **Gerçek kare süreleri:** Animasyonun her karesinin süresini WebP'nin ham ANMF chunk'larından okuyarak hızı bozmadan korur.
- **"Ses kral" senkronu:** Nihai videonun uzunluğu sesin uzunluğuna eşitlenir; animasyon kısaysa döngülenir, uzunsa kesilir, ses asla kesilmez.
- **Kapatınca kaybolmayan indirme listesi:** Linkleri yapıştırırsın, resmi, adı, durumu ve ilerlemesiyle satır olarak sıraya girerler ve bir dahaki açılışta hâlâ oradadırlar. Bkz. [İndirme listesi](#i̇ndirme-listesi).
- **Bildirim alanında yaşar:** Pencereyi kapatmak uygulamayı kapatmaz, indirme sürer; çıkmak istediğinde tepsi ikonunun menüsünden bilerek çıkarsın. Bkz. [Bildirim alanı](#bildirim-alanı).
- **Durdur ve sonra devam et:** Toplu indirme istediğin anda durdurulup sonra kaldığı yerden sürdürülebilir — uygulamayı kapatsan bile, profili yeniden taramadan. Bkz. [Durdurma ve devam etme](#durdurma-ve-devam-etme).
- **Bildirimler:** Panodan link yakalandığında bir kart, indirme başlayınca blerp'in resmiyle Windows bildirimi. Bkz. [Bildirimler](#bildirimler).
- **Cache bakımı:** Options cache'in ne kadar yer tuttuğunu gösterir, tek buton (ya da `--clear-cache`) geri kazanır.
- **Kimlik doğrulama gerektirmez:** Toplu listeleme, Blerp'in açık GraphQL API'sini kullanır.
- **Kalıcı ayarlar:** Çıktı klasörü, üzerine yazma, toplu limit ve özel bir FFmpeg konumu çalıştırmalar arasında hatırlanır — bkz. [Ayarlar](#ayarlar).
- **Panoyu izleme (opsiyonel, GUI):** Kopyalanan bir Blerp soundbite linkini algılar; ya indirmeden önce sorar ya da otomatik indirir.
- **Uygulama içi güncelleme (GUI, paketlenmiş sürüm):** **Check for Updates** butonu GitHub'daki son sürümü çeker, installer'ı indirip uygular — bkz. [Güncelleme](#güncelleme).

## Gereksinimler

- **Python 3.9+**
- **ffmpeg** ve **ffprobe** — ikisi de PATH üzerinde erişilebilir olmalı (harici ikili dosyalar; `requirements.txt`'te yer almaz).
- **Pillow** (`Pillow>=10.3,<13`) — animasyonlu WebP'yi karelere ayırmak için.

## Kurulum

### 1. Kaynak kodu indir

```bash
git clone https://github.com/RumpleSteelSkin/blerp-downloader.git
cd blerp-downloader
```

> Kaynaktan çalıştırmak yerine hazır bir Windows installer'ı mı tercih edersiniz? Bkz. [Paketleme (.exe & installer)](#paketleme-exe--installer) — `BlerpDownloader-Setup-<sürüm>.exe` üretir; ne Python'a ne de bu clone adımına ihtiyaç duyar.

### 2. Python bağımlılığını kur

```bash
pip install -r requirements.txt
# (veya doğrudan)
pip install Pillow
```

### 3. ffmpeg/ffprobe kur

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

Toplu modda dosyalar `<başlık>_<biteId>.mp4` olarak adlandırılır ve var olanlar atlanır (resume). İşlem sonunda `Done: <n> downloaded, <n> skipped, <n> failed -> <çıktı-yolu>` özeti basılır (program çıktısı İngilizcedir).

> **Not:** Hem `--user` (veya bir `/u/` profil URL'si) hem de bir soundbite URL'si birlikte verilirse toplu mod kazanır; tek-blerp URL'si yok sayılır.

### Grafik arayüz (GUI)

Yalnızca Python standart kütüphanesini kullanan (ek bağımlılık yok) basit bir Tkinter arayüzü gelir:

```bash
python blerp_gui.py
```

Tek kutuya bir soundbite URL'si **ya da** kullanıcı adı / profil URL'si yapıştırın (mod otomatik algılanır), isterseniz bir çıktı klasörü ve/veya (PATH'te değilse) bir FFmpeg klasörü seçin, sonra **Download**'a basın. Bir ilerleme çubuğu ve canlı log gösterilir; uzun toplu indirmeler çalışırken **Stop** ile kesilebilir.

İki onay kutusu panoyu izlemeyi etkinleştirir: **"Watch clipboard for Blerp links"** pencere açıkken kopyalanan tek bir soundbite linkini algılar; **"Auto-download (skip confirmation)"** bunun hemen mi yoksa bir onay sorusundan sonra mı indirileceğini belirler. Bu alanların ve onay kutularının hepsi (pencere boyutuyla birlikte) bir sonraki açılış için hatırlanır — bkz. [Ayarlar](#ayarlar).

## Seçenekler

| Argüman | Açıklama |
|---|---|
| `target` (pozisyonel, opsiyonel) | Soundbite URL'si **VEYA** `/u/<kullanıcı>` profil URL'si |
| `--user USERNAME` | Bir kullanıcının TÜM blerp'lerini indir (toplu mod) |
| `-o`, `--out` | Tek mod: çıktı dosyası \| Toplu mod: çıktı klasörü |
| `--limit N` | Toplu modda yalnızca ilk N blerp (`bites[:N]`) |
| `--delay SN` | Toplu modda blerp'ler arası bekleme (saniye, varsayılan: `0.3`) |
| `--overwrite` / `--no-overwrite` | Toplu modda üzerine yaz / kaydedilmiş varsayılanı geçersiz kılıp atla |
| `--resume` / `--no-resume` | Toplu modda kaydedilmiş profil listesini kullan / yeniden tara (varsayılan: açık) |
| `--clear-cache` | İndirilen güncellemeleri, geçici dosyaları ve kaydedilmiş yarım işi sil ve çık |
| `--reset-settings` | Tüm ayarları varsayılana döndür ve çık |

> `--limit`, `--delay` ve `--overwrite` yalnızca toplu modda etkilidir. `-o/--out`, tek modda dosya, toplu modda klasör olarak yorumlanır. `-o`, `--limit`, `--delay` ve `--overwrite`'ın hepsi varsa [Ayarlar](#ayarlar)'da kayıtlı değeri, yoksa yukarıdaki varsayılanları kullanır.

## Güncelleme

GUI'de, bu deponun [Releases](https://github.com/RumpleSteelSkin/blerp-downloader/releases) sayfasını sorgulayan bir **Check for Updates** butonu var.

**Paketlenmiş sürüm (kurulum sihirbazıyla kurulmuş):** Yeni bir sürüm varsa uygulama `BlerpDownloader-Setup-X.Y.Z.exe` dosyasını indirir; siz onayladıktan sonra kendini kapatır ve installer'ı sessizce çalıştırır. Installer dosyaları değiştirir, kısayollarınızı korur ve uygulamayı otomatik olarak tekrar açar. `settings.ini` dosyanız korunur.

**Kaynaktan çalıştırma:** Buton hiçbir şey indirmez ve hiçbir şeyi değiştirmez — bunun yerine `git pull` kullanmanızı söyler; yani checkout'unuza (ve yerel değişikliklerinize) asla dokunulmaz.

Notlar:
- İndirilenler `%LOCALAPPDATA%\BlerpDownloader\updates\` altına iner ve bir hafta sonra otomatik temizlenir. **Stop** butonu devam eden bir güncelleme indirmesini iptal eder.
- **İndirilen dosya çalıştırılmadan önce doğrulanır.** SHA-256'sı release ile yayınlanan `SHA256SUMS.txt` içindeki değere karşı kontrol edilir; ancak eşleşirse yerine taşınır ve ancak o zaman çalıştırılır. Checksum dosyası olmayan bir release güvenilmez sayılıp reddedilir, ve kaydedilen dosya adı sunucunun verdiği addan değil sürüm numarasından türetilir.
- Güncelleme kontrolü GitHub'ın kimlik doğrulamasız API'sini kullanır; bu API saatte IP başına 60 isteğe izin verir. Bu limite takılırsanız uygulama bunu söyler ve Releases sayfasını açmayı önerir.
- Son release'ten *daha yeni* bir sürüm çalıştırıyorsanız (ör. yerel bir geliştirme derlemesi), uygulama bunu bildirir ve sizi geriye "güncellemeyi" reddeder.
- Her release bir `SHA256SUMS.txt` yayınlar; installer'ı elle indirirseniz doğrulayabilirsiniz. Çalıştırılabilir dosyalar imzasız olduğundan, tarayıcıyla indirilen bir installer'ı çalıştırırken Windows SmartScreen uyarı verebilir — hash'i kontrol ettikten sonra "Daha fazla bilgi" → "Yine de çalıştır".

### Release çıkarma (geliştiriciler için)

Release'ler bir Windows runner üzerinde [`.github/workflows/release.yml`](.github/workflows/release.yml) tarafından üretilir:

```bash
# 1. blerp_downloader/__init__.py içindeki __version__ değerini yükselt
# 2. commit'le
git tag v1.1.0
git push --tags
```

Workflow, tag ile `__version__` uyuşuyor mu diye doğrular (yükseltmeyi unuttuysanız build'i sesli şekilde patlatır), her iki çalıştırılabiliri derler, installer'ı üretir ve release'i SHA-256 checksum dosyasıyla birlikte yayınlar. İçinde tire olan bir tag (`v1.1.0-rc.1`) **prerelease** olarak yayınlanır; uygulama içi güncelleyici bunları görmez — pipeline'ı kullanıcılara göndermeden test etmek için kullanışlıdır.

## Durdurma ve devam etme

Toplu indirme sırasında **Stop**'a bastığında o an işlediği blerp'i bitirir ve durur. Sonra **Download**'a tekrar bas — aynı kullanıcı adıyla — kaldığı yerden devam eder. Bu, uygulamayı kapattıktan sonra da, çökme sonrasında da çalışır.

Hızlı olmasının sebebi, sadece dosyaların değil **taramanın** da hatırlanması. Bir profili taramak arka arkaya çok sayıda istek demek (API sayfa başına 12 blerp döndürüyor), yani büyük bir profilde dakikalar; devam ederken önceki çalışmanın listesi yeniden kullanılıyor ve indirme anında başlıyor. Yarıda kesilen bir tarama bile saklanıyor, o ana kadar bulunanlar çöpe gitmiyor.

Listeyi yeniden kullanmak, baştan taramaktan aynı zamanda daha doğru: yeni bir tarama sıralamayı değiştirir, yani `--limit 50` bambaşka bir 50 blerp'i kapsar; sitede adı değişmiş bir blerp de yeni adıyla ikinci kez inerdi.

```bash
python blerp_to_mp4.py --user someone          # yarım kalmış bir iş varsa devam eder
python blerp_to_mp4.py --user someone --no-resume   # yok say ve yeniden tara
```

Kaydedilen iş, indirme sonuna ulaştığında ve 30 gün sonra unutulur. **`--overwrite` çalışmaları asla devam ettirilmez**: zaten indirilmiş olanı bilerek yok saydıkları için ne kadar ilerlediklerini anlamanın bir yolu kalmaz — her zaman ilk blerp'ten başlarlar.

İndirilmiş dosyalar adlarından tanınır, hiçbir şey iki kez indirilmez. Yarım yazılmış bir dosya asla tamamlanmış sanılmaz: her MP4 hedefinin yanında oluşturulur ve ancak bittiğinde yerine taşınır.

## Cache

**Clear cache…** (ya da `--clear-cache`) şunları siler:

- indirilen güncelleme installer'ları (her biri 13-35 MB)
- yarıda kesilen indirmelerden kalan geçici klasörler
- varsa kaydedilmiş yarım iş — onay penceresinde açıkça belirtilir, çünkü kaybolması bir sonraki çalışmanın yeniden taraması demek

İsteğe bağlı olarak çıktı klasöründeki yarım yazılmış `.part` dosyalarını da siler. Bu varsayılan olarak kapalı: zararsızlar ve uygulamanın senin seçtiğin bir klasörün içinde bir şey sildiği tek yer orası.

Kullanımda olan hiçbir şeye dokunulmaz — indirilmekte olan bir installer, devam eden bir indirmeye ait geçici klasör, ikinci bir uygulama kopyasındakiler dahil — tespit edilip atlanır.

**Reset settings…** (ya da `--reset-settings`) bilerek ayrı tutuldu: çıktı klasörü, FFmpeg klasörü, limit, üzerine yazma ve pano seçeneklerini varsayılana döndürür. İndirdiğin dosyalara ve pencere boyutuna dokunmaz.

## İndirme listesi

Ana pencere bir liste. Bir soundbite URL'si — ya da bütün bir profil için bir
kullanıcı adı — yapıştırıp **Add**'e basarsın, satır olur. **Start** satırları
sırayla işler; **Stop** o an inen blerp'i bitirip durur.

Her satırda blerp'in resmi (biliniyorsa), adı, ne yaptığı ve nereye geldiği
görünür. Biten satırlar sen silene kadar durur, yani liste aynı zamanda neyi
aldığının kaydı olur. **Remove** seçili satırları, **Clear finished** bitenleri,
**Clear list…** hepsini kaldırır. İnmekte olan satır kaldırılamaz — önce Stop.

**Liste diske kaydedilir.** Uygulamayı kapat, makineyi yeniden başlat, elektrik
gitsin: satırlar aynen geri gelir. Süreç bittiğinde yarıda kalan bir satır
"sonsuza kadar meşgul" gibi değil, *Bekliyor* olarak geri döner; çünkü yarım
kalmış bir blerp ortasından sürdürülemez, baştan başlar. Bitmemiş bir linkin
kaybolmamasını sağlayan da tam olarak budur.

Profil, binlerce satır değil, tek satırdır. Oka basınca içindeki blerp'ler
açılır; her biri kaydedilmiş ya da bekliyor olarak işaretlidir ve o profil için
daha önce taranmış listeden okunur. Sırf satırı açtın diye hiçbir şey taranmaz.

**Profilden neyin ineceğine sen karar verirsin.** Tarama bittiğinde her blerp'i
işaret kutusuyla listeleyen bir pencere açılır — elinde olmayanlar senin için
zaten işaretli — Select all, Clear all, Invert ve *Only the missing ones*
düğmeleriyle; bir aralığı taramak için shift-tık. Download'a bastığında yalnızca
işaretlediklerin iner. Her satırda blerp'in resmi var; yalnızca kaydırdıkça görünenler getiriliyor —
çünkü o resim animasyonun kendisi, yüzlercesini baştan yüklemek profilin
tamamını sırf almamaya karar vermek için indirmek olurdu. Satıra sağ tıklayıp
o blerp'in sayfasını açabilir ya da URL'sini kopyalayabilirsin.

Seçimin hatırlanır, durdurup tekrar başlattığında ikinci
kez sormaz. Her şeyi almayı tercih ediyorsan Options'tan kapatabilirsin.

Bir satıra sağ tıkla: **Copy URL**, tarayıcıda aç, klasörünü aç, tekrar indir,
listeden çıkar. Profilin içindeki bir blerp'te Copy URL profilin değil, o
blerp'in kendi sayfasını verir.

## Bildirim alanı

Pencereyi kapatmak çıkmak değildir. Uygulama bildirim alanında devam eder, böylece
yanlış zamanda çarpıya basmak yarıda kalan bir indirmeyi mahvedemez. İkonun sağ
tık menüsünde Open, Start, Stop ve Quit var — çıkış bilerek yapılır ve indirme
sürüyorsa önce sorar.

Aynı anda tek kopya çalışır. Uygulamayı tekrar başlatmak, kayıtlı liste üzerinde
onunla çekişecek ikinci bir kopya açmak yerine zaten çalışan kopyayı ekrana geri
getirir.

X'in gerçekten kapatmasını istiyorsan Options'tan kapatabilirsin.

## Bildirimler

İki tür, ikisi de isteğe bağlı.

Pano izleme bir Blerp linki yakaladığında köşede küçük bir kart belirir: blerp'in
resmi, **Add to list** ve **Ignore**. Birkaç saniye sonra kendiliğinden kaybolur,
üzerine geldiğinde bu geri sayım durur, ve yalnızca bir düğmeye basınca iş yapar
— yanlışlıkla tıklamayla asla. Windows'a göre an uygun değilse (tam ekran oyun,
sunum, sessiz saatler) hiç çıkmaz.

Bir indirme başladığında Windows'un kendi bildirimi çıkar: blerp'in adı ve resmi
ile. Satır başına bir tane, bir de çalışma bitince özet — profilin içindeki her
blerp için asla, çünkü 3.000 bildirim bir uygulamanın bildirimlerinin temelli
kapatılmasının yoludur.

## Ayarlar

Çıktı klasörü, üzerine yazma, toplu limit/gecikme, özel bir FFmpeg konumu, pencere boyutu, tema ve pano izleme seçenekleri küçük bir INI dosyasında saklanır (güncellemeler ayrı bir klasör kullanır, bkz. [Güncelleme](#güncelleme)) (Python'un standart kütüphanesindeki `configparser` ile — birkaç anahtar-değer ayarı için bir veritabanı gereğinden fazla olurdu):

- Windows: `%APPDATA%\BlerpDownloader\settings.ini`
- macOS/Linux: `~/.config/blerp-downloader/settings.ini`

**GUI**, açılışta bu dosyayı okuyup alanları doldurur; bir indirme başladığında veya pencere kapatıldığında geri yazar — yani en son ne kullandıysanız yeni varsayılan o olur. **CLI** aynı dosyayı argüman varsayılanları (`-o`, `--limit`, `--delay`, `--overwrite`) için okur ama asla yazmaz; böylece tekrarlanan/scriptlenen CLI çağrıları GUI'nin son kaydettiğinden bağımsız olarak deterministik kalır. Eksik ya da bozuk bir ayar dosyası hiçbir zaman uygulamayı çökertmez — yok sayılır ve yerleşik varsayılanlar kullanılır.

Dosya düz metindir; uygulama kapalıyken elle düzenlemek güvenlidir (ör. bozuk bir `ffmpeg_dir` yolunu düzeltmek için). UTF-8 BOM'u da kabul edilir; BOM ekleyen editörler dosyayı bozmaz.

### Görünüm

Arayüz Windows'un açık/koyu ayarını takip eder; uygulama açıkken ayarı değiştirirsen birkaç saniye içinde başlık çubuğu dahil kendini günceller. Sabitlemek istersen ayar dosyasındaki `theme` değerini `dark` ya da `light` yap (varsayılan `auto`):

```ini
[general]
theme = dark
```

Windows yüksek kontrast modu açıksa uygulama sistem temasına dokunmaz; erişilebilirlik renklerin geçerli kalır. Not: dosya seçme ve mesaj pencereleri Windows'a aittir ve her zaman sistem temasını izler, bu yüzden temayı elle sabitlediğinde pencereyle uyuşmayabilirler.

## Nasıl Çalışır

### Tek-blerp pipeline'ı

1. **[1/5] Sayfa taranır:** URL içindeki 24 karakterlik ObjectId çözülür, sayfa bir tarayıcı User-Agent'ı ile indirilir, `<script id="__NEXT_DATA__">` JSON'u çıkarılır. `props.pageProps.initialApolloState` içinden `Bite:<id>` nesnesi (yoksa ilk `Bite:` anahtarı) bulunur; `audio.mp3.url` ve `image.original.url` Apollo `__ref` işaretçileri çözülerek elde edilir.
2. **[2/5] Medya indirilir:** Görsel `image.webp`, ses `audio.mp3` olarak geçici bir klasöre yazılır.
3. **[3/5] Kareler çıkarılır:** WebP, Pillow ile PNG karelere (`frame_00000.png`...) ayrılır; her karenin gerçek süresi ham ANMF chunk'larından okunur, eksik süreler 40ms (~25fps) varsayılır.
4. **[4/5] Animasyon videosu kurulur:** Bir concat demuxer listesi yazılır (son kare iki kez eklenir, çünkü concat son sürenin değerini yok sayar) ve `ffmpeg ... -vsync vfr -c:v libx264 -pix_fmt yuv420p` ile sessiz bir h264 MP4 üretilir.
5. **[5/5] Senkron + birleştirme:** Sesin gerçek uzunluğu `ffprobe` ile ölçülür, `SyncPlan` kurulur ve `ffmpeg` ile görsel + ses son MP4'e mux edilir.

### Toplu listeleme (GraphQL)

- Önce `userByUsername` sorgusuyla kullanıcının `_id`'si bulunur (kullanıcı yoksa `User not found: <username>` hatası).
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
- **Konsol/kodlama:** stdout/stderr UTF-8'e yeniden yapılandırılır; bu yüzden Windows konsolu (cp1252) `•`, `✓`, `✗` gibi simgelerde çökmez.

## Paketleme (.exe & installer)

Bağımsız Windows çalıştırılabilirleri [PyInstaller](https://pyinstaller.org) ile üretilir:

```bash
pip install pyinstaller
python generate_logo.py   # assets/icon.ico'yu üretir (yalnızca bir kez gerekli)
python build.py
```

`dist/BlerpDownloader/` klasörünü üretir (dosya özelliklerinde *By RumpleSteelSkin* imzalı); içinde her iki program ve ortak kullandıkları Python runtime'ı bulunur:

- **`BlerpDownloader.exe`** — GUI (pencereli)
- **`blerp.exe`** — komut satırı aracı
- **`_internal/`** — ortak runtime

Bu bilinçli olarak tek-dosya değil klasör paketlemesi. Tek-dosya exe her açılışta tüm runtime'ını `%TEMP%` altına açar; bu adıma bir şey karışırsa (yeni yazılmış imzasız bir exe'yi tarayan antivirüs, temp temizliği) uygulama *"Failed to load Python DLL"* ile ölür. Klasör paketlemesinde açılacak bir şey yoktur; daha hızlı başlar ve bu hata mümkün değildir. Runtime'ın iki exe arasında paylaşılması installer'ı da küçültür. Ödünü: exe'ler yanlarındaki klasöre ihtiyaç duyar, yani sadece `.exe`'yi kopyalayıp başka yere taşımak çalışmaz.

> ffmpeg/ffprobe **gömülmez**; hedef makinede `PATH` üzerinde (ya da [Ayarlar](#ayarlar)'da belirtilen klasörde) bulunmalıdır.

Windows kurulum sihirbazı için [Inno Setup 6](https://jrsoftware.org/isinfo.php) kurun (`winget install JRSoftware.InnoSetup`) ve dahil edilen betiği derleyin:

```bash
ISCC installer.iss
```

Kurulum dosyası (`dist/installer/BlerpDownloader-Setup-<sürüm>.exe`) her iki exe'yi kurar, Başlat Menüsü / masaüstü kısayolları oluşturur ve yayıncı olarak **RumpleSteelSkin**'i gösterir. Kurulum **kullanıcı bazlıdır (yönetici sormaz)** ve ffmpeg `PATH`'te yoksa kurulum sırasında **winget** ile otomatik kurar — yani son kullanıcının **ne Python'a ne de ffmpeg'e** elle ihtiyacı olur. (winget yoksa installer ffmpeg indirme linkini gösterir.)

## Geliştirme

```bash
python -m unittest discover tests        # tüm testler
python -m unittest tests.test_scraping   # tek modül
```

Testler her push ve pull request'te ([`ci.yml`](.github/workflows/ci.yml)) Python 3.9 ve 3.12 üzerinde, ayrıca her release derlenmeden önce çalışır — testler kırıkken release yayınlanamaz.

`tests/fixtures/` blerp.com yanıtlarının yakalanmış hallerini tutar: soundbite sayfasının gömdüğü `__NEXT_DATA__` bloğu ve sayfalanmış bir profil listesi. Amaçları, sitenin yapısı değişirse bunun kullanıcının hata bildirimi yerine commit anında bir testi düşürmesi — yani scraping bozulursa ilk adım fixture'ı güncellemektir.

## Sorun Giderme

- **`ERROR: Pillow is required.`** — `pip install Pillow` çalıştırın.
- **FFmpeg bulunamadı** — uygulama bunu algılar ve çökmek yerine sizi yönlendirir (CLI çözümü yazdırır; GUI winget ile kurmayı önerir). En hızlısı: `winget install Gyan.FFmpeg`, sonra **uygulamayı yeniden başlatın**. Doğrulama: `ffmpeg -version` / `ffprobe -version`. Alternatif: <https://ffmpeg.org/download.html> adresinden indirip `PATH`'e ekleyin ya da `choco install ffmpeg` / `scoop install ffmpeg` — ya da PATH'e eklemek istemediğiniz bir yere kurduysanız, GUI'deki "FFmpeg folder" alanını (veya [Ayarlar](#ayarlar)'daki `ffmpeg_dir`'i) o klasöre işaret edin.
- **`HTTP 403` / indirilemedi** — site/CDN varsayılan urllib User-Agent'ını engeller; betik zaten tarayıcı UA'sı gönderir. Hata sürerse ağ/erişim sorununu kontrol edin. Betikte ağ yeniden deneme/backoff yoktur; tek modda hata programı bitirir, toplu modda yalnızca o blerp atlanır.
- **`__NEXT_DATA__ not found on the page (the site structure may have changed).`** — Tek-mod scraping'i sitenin `__NEXT_DATA__`/Apollo yapısına bağlıdır; site yapısı değişmiş olabilir.
- **`User not found: <username>`** — Toplu modda kullanıcı adı hatalı ya da kullanıcı yok.
- **`No audio/image URL found for this blerp.`** — Beklenen `audio.mp3.url`/`image.original.url` alanları bulunamadı. Toplu modda, medyası eksik öğeler sessizce listeden düşürülür.
- **`Cancelled.`** — İşlem Ctrl+C ile durduruldu.
- **Statik / WebP olmayan görsel:** ANMF süreleri okunamazsa Pillow + 40ms varsayılan süre ile tek/çok kare yine de işlenir.

## Yasal Uyarı

Bu araç yalnızca Blerp'in hizmet şartlarına (ToS) uygun şekilde ve indirme hakkına sahip olduğunuz içerik için kullanılmalıdır. İndirilen içeriğin telif hakları ve kullanım koşulları size aittir; üçüncü taraflara ait içeriği izinsiz indirmek, dağıtmak ya da yeniden yayımlamak sizin sorumluluğunuzdadır. Toplu modda `--delay` ile istekler arasında bekleme bırakarak servise nazik davranın.

# Luna IPTV 0.2.0 doğrulama kaydı

2026-09-05 · openSUSE Tumbleweed · GNOME Wayland. Aynı Qt Widgets/QOpenGLWidget ve tek libmpv handle korunmuştur; yeni çalışma zamanı bağımlılığı eklenmemiştir.

## Sonuçlar

- **163 test geçti** (`-W error`), Ruff lint ve format temiz. Özgün 45 testin bulunduğu 8 dosya önceki checkpoint ile byte düzeyinde aynı; yeni davranışlar ayrı test dosyalarıyla sınanır.
- Native yerel MPEG-2/MP2 ve localhost HLS probe başarılı: görüntü, ses parçası, pause/seek/mute ve HTTP başlıkları doğrulandı.
- Ayrı native H.264/AAC dosya + HLS probe başarılı. Bütün native çalıştırmalarda `DISPLAY`/`GDK_BACKEND` kaldırıldı ve Qt platformu `wayland` döndü.
- Mevcut tam GUI smoke başarılı: M3U/XMLTV import, rehber, favori, tam ekran, VOD stop/replay ve kalıcı veri.
- Yeni birleşik GUI probe: gerçek 1280 × 720 / HD · 720p / H.264 / AAC / 25 FPS; renkli framebuffer. Bilgi paneli ve buffer etiketi pencere genişliğini değiştirmedi. Geçersiz logo baş harfe döndü, geçerli iki logo birer kez indirildi; logo işlemi stream açmadı.
- Yerel Xtream fixture: tek profil isteği, aktif durum, 90 gün ve 1/2 bağlantı. Gerçek kullanıcı hesabına erişilmedi. `%37` buffer kontrolü UI property simülasyonudur; gerçek sağlayıcı tıkanması ölçümü değildir.
- Logo testleri: timeout/redirect/HTTP hata, boyut/decode sınırları, memory/disk TTL-LRU-kota, yarım yazı, yerel dosya/FIFO, görünür satır önceliği ve kapanış. Qt TLS desteği yerelde doğrulandı.
- Hesap testleri: whitelist, eski DB, FK silme, eksik/bozuk/büyük sayılar, tarih sınırları, tam sayı dönüşüm maliyeti, Escape/done/close ve geç başarı/hata yanıtları. Yenileme başarısızlığında önceki snapshot ve kontrol zamanı korunur.

## Performans: aynı bilgisayar, sentetik fixture

Karşılaştırma tabanı `f74c6695dc79d5d15cf9832b38f4d4e0f4322a32` (0.1.0). Ölçümler internet gecikmesi veya bütün sağlayıcılar için garanti değildir.

| Ölçüm | 0.1.0 | 0.2.0 |
|---|---:|---:|
| 10 bin satır, sonraki arama (`bulunmayan`) | 51,20 ms | 14,50 ms |
| 50 bin satır, sonraki arama | 251,48 ms | 77,25 ms |
| 100 bin satır, sonraki arama | 506,69 ms | 154,16 ms |
| 100 bin satır, ilk eşleşen sorgu | 993,62 ms | 313,32 ms |
| Yerel kanal değişimi, ortanca görünür kare süresi | 60,59 ms | 62,92 ms |
| Yerel kanal değişimi, p95 | 61,26 ms | 63,92 ms |

Arama yaklaşık 3,3 kat hızlandı. Karşılığı ilk katalog resetinde yaklaşık 270 ms normalize etme işi ve 100 bin örnek için yaklaşık 7,33 MiB anahtar listesi/metin boyutu (allocator/Qt/kanal nesneleri hariç; RSS değildir). Anahtarlar katalog değiştiğinde yeniden hazırlanır; her tuşta hesaplanmaz.

Kanal değişiminde ortanca fark +2,33 ms, p95 fark +2,66 ms; bu fixture'da hızlı geçiş korunmuştur. 640 × 360 / 25 FPS iki yerel MPEG-2 renk fixture'ı, 4 ısınma + 20 ardışık değişim, gerçek MainWindow.play çağrısından hedef framebuffer renginin doğrulanmasına kadar ölçülür. Aynı libmpv handle bütün değişimlerde korunur. Her iki checkout kendi gerçek `apply_theme()` fonksiyonunu kullanır. İlk teması uygulanmamış benchmark'ta görülen bir karelik periyodik gecikme, gerçek uygulamayı temsil etmediğinden son karşılaştırmaya alınmadı; ham tanı kayıtları korunur. Oynatıcı/render kodu bu tanı için değiştirilmedi.

## Paket

`dist/luna-iptv-0.2.0-1.noarch.rpm`, kaynak RPM ve tarball üretildi. Çıkarılan RPM içindeki bütün Python modülleri çalışma ağacıyla byte düzeyinde eşleşti; launcher `Luna IPTV 0.2.0` döndürdü ve paket modülleriyle izole native Wayland penceresi açıldı. Masaüstü girdisi ve bağımlılık metadata'sı doğrulandı. Kaynak arşivinde çalışma ortamı, cache, veritabanı, gerçek hesap bilgisi veya build klasörü yoktur. SHA-256 listesi `dist/SHA256SUMS` içindedir.

Sisteme kurulum yapılmadı. Paket yerel ve imzasızdır; Leap/başka GPU veya gerçek sağlayıcı uçtan uca uyumluluğu ayrıca doğrulanmış sayılmaz. Eksik bitrate/HDR alanları bilinmiyor kalır; HDR etiketi kaynağı anlatır, monitör çıkışını değil.

## Tekrar çalıştırma ve kanıtlar

```bash
env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh
LD_LIBRARY_PATH="$PWD/work/deps/root/usr/lib64" QT_QPA_PLATFORM=wayland env -u DISPLAY -u GDK_BACKEND .venv/bin/python scripts/balanced_probe.py
LD_LIBRARY_PATH="$PWD/work/deps/root/usr/lib64" QT_QPA_PLATFORM=wayland env -u DISPLAY -u GDK_BACKEND .venv/bin/python scripts/benchmark-zapping.py
LD_LIBRARY_PATH="$PWD/work/deps/root/usr/lib64" .venv/bin/python scripts/benchmark-search.py
./scripts/build-rpm.sh
LD_LIBRARY_PATH="$PWD/work/deps/root/usr/lib64" QT_QPA_PLATFORM=wayland env -u DISPLAY -u GDK_BACKEND .venv/bin/python scripts/check-package.py
```

İndirilen yerel runtime kullanılmıyorsa `LD_LIBRARY_PATH` ataması gerekli değildir. Sonuçlar: `work/qa/balanced-full-tests.log`, `balanced/result.json`, `balanced-h264/result.json`, `search-before-same-harness.json`, `search-final.json`, `zapping-before-themed.json`, `zapping-after-themed.json`, `package/result.json`. Ekranlar `work/qa/balanced/` içindedir. Bunlar repoya veya kaynak arşivine girmez. GitHub CI henüz yapılandırılmadı; raporlanan testler bu makinede çalıştırıldı.

Özellikler issue/branch/bağımsız inceleme/test/PR akışıyla geliştirildi: #8–#13 özellik PR'ları; #2 sürüm takibi. Orijinal checkpoint geçmişi korunmuştur.

---

# Luna IPTV 0.1.0 doğrulama kaydı

2026-09-05 · openSUSE Tumbleweed 20260902 · GNOME Wayland · Python 3.13 · PySide6 6.11.2 · python-mpv 1.0.8.

## Gerçek masaüstü ve medya

Bütün native doğrulamalar `QT_QPA_PLATFORM=wayland` ile, `DISPLAY` ve `GDK_BACKEND` ortamdan çıkarılarak yürütüldü. Qt platformu `wayland`. XWayland kullanılmadan libmpv'nin Qt OpenGL framebuffer'ına renkli gerçek video çizdiği kontrol edildi. Test medyası FFmpeg ile yerelde üretilmiştir.

| Kontrol | Kanıt |
|---|---|
| Parser, XMLTV, depolama, ağ, Qt kullanıcı akışları, native oynatıcı, fullscreen | pytest: 45 test; warnings-as-errors |
| Yerel video ve localhost HLS | Gerçek `file-loaded`, ilerleyen süre, pause/seek/mute, video+audio tracks |
| Sağlayıcı HTTP başlıkları | HLS manifest/segment isteklerinde virgül içeren custom header doğrulandı |
| H.264/AAC | Ayrı native yerel dosya + HLS probe |
| Görüntü çizimi | Framebuffer örneklemesinde çok sayıda farklı renk; PNG görsel kontrolü |
| Gerçek pencere akışı | Async M3U + XMLTV; tıklayarak oynatma, rehber, favori, tam ekran, VOD resume |
| Devam etme | 45 saniyelik fixture'da 12. saniyeden başlatma, stop→oynat, kapatma→yeniden açma |
| GNOME fullscreen | Üç hızlı giriş/çıkış turunda geciken compositor yanıtı son kullanıcı isteğini bozmuyor |
| Kaynak yenileme | Bölüm favori/ilerlemesi korunuyor; yenilenen URL current/retry'a yansıyor |
| QObject ömrü | Pencere/video ve biten iş sinyalleri GUI thread içinde silinir; worker GC regresyonu |
| Silme/kapanış | Oynayan kayıt güvenle temizleniyor; SQLite FK hatası veya geç callback yok |
| Türkçe arama | İ/i ve I/ı varyasyonları eşleşiyor |
| Format/lint | Ruff format ve E4/E7/E9/F/I/B kuralları |

Üretilen makine kanıtları (kaynak dağıtımına dahil edilmez):

- `work/qa/player/result.json`: MPEG-2/MP2 dosya ve HLS render probe.
- `work/qa/h264/result.json`: H.264/AAC dosya ve HLS render probe.
- `work/qa/app/result.json`: gerçek MainWindow uçtan uca akışı.
- `work/qa/review/result.json`: bağımsız review sonrası resume/katalog güvenliği.
- `work/qa/app/luna-empty.png`, `luna-playing.png`: gerçek uygulama görüntüleri; yayın görünen kare sentetik testtir.

## Paket kontrolü

`./scripts/build-rpm.sh` root yetkisi ve ağ kullanmadan RPM + kaynak RPM + kaynak tarball üretir. RPM noarch'tır. Python >=3.11, PySide6 >=6.8,<6.12, python-mpv >=1.0.8,<2 ve libmpv2 >=0.38 bağımlılıkları açıkça belirtilir. Masaüstü girdisi, özgün SVG simge, Python modülleri, README ve MIT lisansı pakettedir.

`rpmbuild` %check içinde Python derleme/sözdizimi ve desktop-file-validate kontrollerini yürütür. RPM bağımlılıkları ve içeriği incelendi; çıkarılan paketin launcher'ı `--version` ile sınandı. Native GUI ayrıca çıkarılmış paket modülleriyle başlatılır. Kaynak tarball'ında venv, çalışma klasörü, yerel veritabanı, medya veya indirilen çalışma zamanı kütüphaneleri yoktur. Dağıtım dosyalarının SHA-256 listesi `dist/SHA256SUMS` içinde üretilir.

Tam sistem kurulumu yapılmadı; bu ortamda sudo parola gerektiriyor. Paket çıkarma/başlatma ve bağımlılık metadata'sı doğrulandı. RPM yerel, imzasız bir geliştirme çıktısıdır. Leap, diğer GPU/sürücüler ve gerçek sağlayıcı hesapları için doğrulama iddiası yoktur.

## Tekrar çalıştırma

```bash
env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh
LD_LIBRARY_PATH="$PWD/work/deps/root/usr/lib64" QT_QPA_PLATFORM=wayland \
  env -u DISPLAY -u GDK_BACKEND .venv/bin/python scripts/player_probe.py --h264 --output work/qa/h264
./scripts/build-rpm.sh
```

Gerçek Xtream hesabı verilmediği için katalog, kimlik doğrulama ve sezon/bölüm uyumluluğu yerel HTTP fixture ile sınanmıştır. Uygulama kayıt, catch-up/time-shift, çoklu ekran, DRM veya ebeveyn kilidi içermez. Geçersiz/boş import mevcut kütüphaneyi korur. URL/EPG boyut sınırı 64 MiB, ağ I/O timeout'u 20 saniyedir; XML iç DTD/entity bildirimleri reddedilir; normal harici DOCTYPE çözülmeden kabul edilir, timezone yoksa UTC kullanılır. Şifreler ve yayın adresleri yerel 0600 veritabanında ek şifreleme olmadan tutulur; telemetri yoktur.

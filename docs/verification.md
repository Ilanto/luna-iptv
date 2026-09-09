# Luna IPTV 0.6.0 · #33 oynatma öncesi diller

2026-09-09 · mevcut TrackPreferences ile dil seçimi ve kaynak bazında hatırlama. Yeni çalışma zamanı bağımlılığı ve veri şeması değişikliği yok; mevcut tercih JSON'u Otomatik seçimini de kabul eder.

- `TrackPreferences.begin()` açık oynatıcıya global aid/sid yazmaz; onaylanmış seçimden dosyaya özel `alang/slang/aid/sid` seçenekleri üretir. Altyazı dili istendiğinde farklı dile ve forced altyazıya fallback kapatılır. Otomatik davranışı kurulu mpv varsayılanlarıyla (`subs-fallback=default`, forced/matching-audio=yes) korunur.
- Hedefli testler **64 geçti / 5,97 sn**. Ardından ilk native seçim için İngilizce ses + Türkçe altyazı kombinasyonu da eklendi; yalnız değişen native test yeniden çalıştırıldı: **1 geçti / 2,54 sn**. İlk lint geçişinde bulunan bir döngü-lambda bağlama hatası testler başlamadan düzeltildi.
- Native HTTP regresyonu ilk `playback_loaded` olayındaki otoritatif parça listesinde Türkçe/Türkçe, İngilizce/İngilizce, İngilizce/Türkçe, olmayan dil, Kapalı ve Otomatik davranışını doğrular; yükleme sonrası tercih uygulaması çağırarak başlangıç hatalarını gizlemez. Her yüklemede **bir GET**, toplamda ek probe/HEAD yok; aynı render bağlamı korunur.
- Gerçek Qt kartında İngilizce ses + Türkçe altyazı hazırlanırken açık Türkçe ses/İngilizce altyazı yayını ve kayıtlı tercihler değişmedi. Bölüm B'ye seçim korundu; devam sorusunu iptal etmek açık yayını korudu. **8,042 sn** devam ve baştan başlatmada İngilizce/Türkçe kaldı. Almanca parçaları olmayan içerikte varsayılan İngilizce ses ve kapalı altyazı, görünür kısa bilgiyle doğrulandı. `wayland`, `DISPLAY_present: false`, aynı mpv/GL bağlamı ve `success:true`; yerel kanıt `work/qa/preplay-native-result.json`, `preplay-card.png`.
- Nesil kimliğiyle etiketli parça listeleri, eski yayın olaylarının yeni tercihlere uygulanmasını engeller. Kapatma/iptal kalıcılığı değiştirmez; `remember=false` tek oynatmalık seçimleri uygular, önceki kayıtlı dilleri silmez; aynı içerikte devam/stop/yeniden deneme için aktif snapshot korunur.
- Paket kontrol yardımcısı çıkarılan paketi launcher'dan önce import ederek `/usr/share` altındaki eski kurulumun sürüm kontrolünü gölgelemesini engeller. Kurulu uygulama dosyaları test için değiştirilmez.
- Sınırlar: gerçek sağlayıcıların tüm dil etiketleri, farklı GPU/compositor'lar ve fiziksel masaüstü girdileri kapsam dışıdır. Dil seçimi mpv'nin bildirdiği dil etiketlerine dayanır; bilinmeyen parça listesi mevcut dil envanteri olarak sunulmaz.

---

# Luna IPTV 0.5.0 · PR #32 inceleme düzeltmeleri

2026-09-09 · bölüm/dizi metadata ayrımı ve sağlayıcı hata yanıtları düzeltildi. Kullanıcı bu aşamada merge ve mevcut kurulumun yükseltilmesine izin verdi; aşağıdaki ilk deneme kaydı o aşamanın tarihsel sınırlarını anlatır.

- A bölümünden B'ye seçim, görünen başlık/açıklama/puan/IMDb/afiş ile Oynat ve bölüm favorisini aynı B kimliğine geçirir. Dizi metadata ve dizi favorisi ayrı **Dizi bilgileri** alanındadır; eksik bölüm alanları dizi veya önceki bölümden doldurulmaz.
- VOD `auth: 0`, explicit hata ve başarısız durum yanıtları `NetworkError` üretir; eski metadata ve kontrol zamanı korunur, **Yeniden dene** kullanılabilir. Geçerli fakat eksik `info`/`movie_data` metadata kabul edilir. Yeni cache yükü tüm bölüm bilgilerini kararlı provider kimlikleriyle ayrı tutar; eski birleştirilmiş payload cache miss olur. Kaynak/favori/geçmiş/konum tabloları dönüştürülmez.
- Hedefli tur: **121 geçti, 2 hata**; hatalar iki yeni testin Qt enum'larını instance üzerinden çağırmasındaydı. `QFormLayout.ItemRole` kullanımı düzeltildi; yalnız bu iki test yeniden çalıştırıldı, **2 geçti / 0,23 sn**.
- Düzeltmeler tamamlandıktan sonra tam komut **bir kez** çalıştırıldı: `env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh` → **425 geçti / 59,87 sn**, Ruff lint/format başarılı. Yerel/HLS probe'ları **94**, pencere smoke'u **86** framebuffer rengi; her biri `wayland`, `DISPLAY_present: false`, `success: true`. Başarılı tam paket tekrar edilmedi.
- Ayrı yerel HTTP/native Qt senaryosu: A kartında B seçimi ve favorisi, ayrı dizi açıklaması, tek metadata isteğiyle önbellekten yeniden açılış, Vazgeç'in canlı yayını koruması, **8 sn'den Devam et**, **0 sn'den Baştan başlat** ve 75 ms aralıklı altı kanal geçişi doğrulandı. Aynı mpv/GL bağlamı korundu. Yerel kanıtlar `work/qa/review-native-result.json` ve `review-selected-b.png`; fiziksel masaüstü girdisi veya internet gecikmesi ölçümü değildir.
- Sürüm `0.5.0`; yeni çalışma zamanı bağımlılığı yok. 0.4.0 üzerine ilk açılışta yalnız ayrıntı önbelleği tablosu eklenir. Yükseltme öncesinde özel veri dizini Git dışında kopyalandı, SQLite backup API ile tutarlı kopya alındı ve mevcut tabloların içerik özetleri karşılaştırıldı; yedek dizinleri 0700, dosyalar 0600 izinli.

---

# Film/dizi ayrıntı kartı · #31

2026-09-09 · ayrı `feat/media-detail-card` çalışma ağacı; kurulu uygulama ve kişisel kütüphane kullanılmadı. Yeni çalışma zamanı bağımlılığı yok.

- Gerçek `MainWindow`, Qt'nin native `wayland` platformunda, `DISPLAY` kaldırılarak ve geçici SQLite diziniyle çalıştırıldı. Yerel HTTP sunucusu gecikmeli sentetik Xtream metadata ve FFmpeg üretimi 640 × 360 MPEG-2/MP2 video sundu; gerçek sağlayıcı bilgisi kullanılmadı.
- Film kartı açılmadan önce canlı konum **15,875 sn**, kart ve afiş geldikten sonra **32,917 sn**; video yüzeyinin frameSwapped sayacı **75 → 250** ilerledi. Aynı mpv nesnesi, OpenGL bağlamı ve oynatma kimliği korundu. Karttan favori işlemi ve kapatma yayını değiştirmedi.
- Film ve dizi kartlarının yeniden açılması toplam **bir get_vod_info + bir get_series_info** isteğiyle tamamlandı; kartların önbellekten açıldığı gözlendi. İki sezondan ikinci sezonun bölümü seçildi; Oynat sonrası **Devam et / Baştan başlat / Vazgeç** penceresi görsel olarak incelendi. Vazgeç canlı yayını korudu; 24 sn kaydından devam edilen bölümde **27,042 sn**, baştan başlatmadan 1,2 sn sonra **0,75 sn** gözlendi.
- 75 ms aralıklarla altı canlı kanal seçimi sonunda son kanal seçiliydi, yüklenme tamamlandı, kare sayacı ilerledi ve aynı mpv/GL bağlamı korundu. Bu bir davranış smoke kontrolüdür; önce/sonra gecikme karşılaştırması veya p95 performans ölçümü değildir.
- Film, dizi, devam seçimi ve **540 × 400** mantıksal boyuttaki eksik bilgi/afiş kartı gerçek Qt ekran görüntüleriyle incelendi. Eksik afiş ve açıklama açıkça belirtildi, Oynat kullanılabilir kaldı. Görseller çalışma ağacının `work/qa/detail-*.png` dosyalarında, Git dışında tutuldu.
- Regresyon kapsamı: puan kaynağı/IMDb kimlik ve bağlantı güvenliği, eksik ve bozuk sağlayıcı alanları, sezon/bölüm sırası, kalıcı önbellek ve bağlantı izolasyonu, eski yanıtların elenmesi, sınırlı istek kuyruğu, hata sonrası eski bilgi/bölümler, kapatma ve silme, afiş boyutu/yaşam döngüsü, favori ve devam seçimi.
- İlk tam test turu native tercih testi sırasında **SIGABRT** ile kesildi. Core dökümünde mpv iş parçacığındaki Python GC'nin `QOpenGLWidget` yıkıcısından `makeCurrent()` çağırdığı görüldü. Önceki bağımsız renderer testinin kapanışında widget yalnız gizleniyordu; test artık `WA_DeleteOnClose` ve GUI-thread deferred-delete ile yüzeyi gerçekten yok ediyor ve bunu doğruluyor. Üretim oynatıcı/renderer kodu değiştirilmedi. Bu düzeltmeden sonra renderer kapanış testi ve native tercih/devam testi birlikte **2 geçti, 2,62 sn**. Yeni hata ve test düzeltmesi nedeniyle tam tur tekrarlandı; başarılı sonucu tekrar çalıştırılmayacak.
- Düzeltme sonrası tam komut: `env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh` → **371 geçti, 58,23 sn**, `-W error`; Ruff lint/format başarılı. Yerel ve HLS probe'larında **94 renk**, özel HLS başlıkları ve seek/pause/mute; tam pencere smoke'unda **99 renk**, M3U/XMLTV, favori, tam ekran, VOD stop/replay ve yeniden açılış doğrulandı. Her iki probe `platform: wayland`, `DISPLAY_present: false`, `success: true` döndürdü. Başarılı tam tur tekrarlanmadı.
- MCP GUI başlatıcısı depo için gereken `LD_LIBRARY_PATH` ortamını aktarmadığı için `libgthread-2.0.so.0` yüklenemedi. Görsel doğrulama bunun yerine aynı depo bağımlılıklarıyla doğrudan native Qt sürecinde yapıldı; girişler Qt düğme/sinyal çağrılarıdır, fiziksel fare/klavye testi olarak sunulmaz.
- Doğrulanmayanlar: gerçek abonelik sağlayıcısı ve bütün Xtream varyantları, gerçek IMDb kaydının varlığı (yalnız kimlik biçimi ve güvenli hedef URL), internet üzerinden afiş/metadata gecikmesi, farklı GPU/compositor ortamları. Sistem paketi kurulmadı veya güncellenmedi; bu denemenin PR'ı merge edilmeyecek.

---

# Luna IPTV 0.4.0 doğrulama kaydı

2026-09-05 · openSUSE Tumbleweed · GNOME Wayland · Python 3.13 · Qt/PySide6 6.11.2 · python-mpv 1.0.8. Yeni çalışma zamanı bağımlılığı eklenmedi; tek mpv oynatıcısı ve mevcut Qt OpenGL yüzeyi korunur.

## Birleşik özellik doğrulaması

- `env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh`: **313 test geçti, 57,92 saniye**, `-W error`; Ruff lint/format, üretilmiş yerel video/localhost HLS probe ve tam pencere smoke başarılı. Bu koşu `c69299190a3cedcbe5c88be7fe30425b6d9802b5` üzerinde bütün özellikler birleştirildikten sonradır.
- 0.3.0 tabanındaki **205 vaka** ve ilk sürümün **45 vakası** korunur. `f5b7db9` tabanındaki 150, `f74c669` tabanındaki 43 özgün test fonksiyonunun gövde, decorator ve imza AST'leri değişmemiştir. Parametrizasyon vaka sayısını artırır. Eklenen işlevlerle toplam 251 test fonksiyonu / 313 vaka vardır.
- Kaynak düzenleme: aday doğrulama, iptal/geç yanıt, atomik DB rollback, Xtream kimliklerinin sonraki yenileme ve bölüm yüklemelerinde korunması, değişen adresler, kaldırılmış oynayan kaydın ayrılması, profil/prefix/HEAD sağlık kontrolü ve sır içermeyen hata mesajları. Testler geçici DB ve yerel HTTP sunucusu kullanır.
- Canlı toparlanma: 1/2/4 saniye beklemeli üç deneme, bağlantı/buffer sınırı, kararlı ilerleme sonrası bütçe sıfırlama, pause/VOD sonu, stop/zap/iptal/düzenleme/kapanış, eski veya eksik mpv yanıtları sınanır. Yükleme kimliği komut gönderilmeden kaydedilir. Takip desteklenmediğinde otomatik deneme devre dışıdır ve bekleme yine sınırlıdır. Yerel HTTP bağlantı hatasından başarıya geçişte aynı backend/GL bağlamı kullanılır.
- Ses/altyazı: değişen parça numaralarında dil eşleşmesi, Kapalı, eksik tercih, kullanıcı seçimi/varsayılan ayrımı, sıfırlama ve kaynak izolasyonu; gerçek çok parçalı yerel dosyada Türkçe ses ve altyazı kapalı seçiminin korunması doğrulandı.
- Devam etme: anlamlı film/bölüm konumu için oynatma öncesi Devam et / Baştan başlat / Vazgeç; iptalde mevcut yayın korunur. Canlı seçim ve aynı yayındaki stop→Play tekrar sormaz. Yüklenme hatası devam konumunu silmez.
- Geçmiş temizleme: seçili/tüm kaynaklar, konumları isteğe bağlı sıfırlama; favoriler ve kaynaklar korunur. Devam eden yayın, kapanış veya otomatik yeniden deneme temizlenmiş geçmişi geri yazmaz; yeni açık kullanıcı oynatma seçimi kaydı yeniden başlatır.
- Mini: normal/minimum pencere, mini/tam ekran/Esc/dönüş ve geciken native compositor yanıtları; aynı video parent/context/backend, renkli framebuffer, ±5 saniye/play/mute, uzun durum metni ve iptal düğmesinin 480 × 300 düzene sığması sınanır. Qt olaylarıyla UI kontrolü fiziksel masaüstü girdi testi olarak sunulmaz. GNOME'de her zaman üstte kalma iddiası yoktur.
- Bir test çalıştırmasında arka plan GC'sine kalan eski test penceresi Shiboken kapanış hatasına yol açtı. İlgili test fixture'ı üretim penceresi gibi `WA_DeleteOnClose` kullanacak şekilde düzeltildi; GUI thread'de silinmeyi doğrulayan regresyon eklendi. Üretim renderer'ı bu test ömrü sorunu için değiştirilmedi. Sonraki bütünleşik 252, 270 ve 313 vakalık koşular düzenli tamamlandı.
- Üretim native callback'leri eski yükleme kimliklerini filtreler; genel komut hatası yayının bitmesi sayılmaz. Önceki GL_BLEND/altyazı, sarma, tam ekran ve QObject yaşam döngüsü regresyonları da geçer.

## Sürüm ve paket doğrulaması

- 0.4.0 sürüm dosyalarıyla tam koşu yeniden tamamlandı: **313 test, 57,83 saniye**, Ruff ve bütün yerel/HLS/GUI probe'ları başarılı (`work/qa/release-full.log`).
- Ayrı görsel kontrolde gerçek pencere **1200 × 760 → 560 × 390 mini → 2304 × 1296 tam ekran → mini → normal** akışını tamamladı. Her adımda aynı backend/context/parent ve renkli framebuffer doğrulandı. Devam etme ve geçmiş temizleme pencereleri gerçek UI girişlerinden açılıp görsel olarak incelendi. Kanıtlar `work/qa/release-040-visual/` altında; medya 30 saniyelik yerel test görüntüsü ve sesi kapatılmış PCM sesidir.
- `./scripts/build-rpm.sh` ile **luna-iptv-0.4.0-1.noarch.rpm**, kaynak RPM ve tarball üretildi. `%check` derleme ve desktop doğrulamasını geçti. `scripts/check-package.py`, çıkarılan paketteki bütün Python modüllerinin kaynakla aynı olduğunu ve launcher'ın `Luna IPTV 0.4.0` döndürdüğünü doğruladı; paket modülleriyle izole native Wayland penceresi açıldı (`DISPLAY_present: false`).
- Paket bağımlılıklarının hem alt hem üst sınırları, desktop girdisi, SVG simge, README ve LICENSE yolları incelendi. Kaynak arşivi dosya listesinde venv, çalışma/build/cache klasörü, DB, medya veya hesap dosyası yoktur. SHA-256 listesi paket üretimi sonrasında ayrıca `dist/SHA256SUMS` içine yazılır; build betiği bunu otomatik oluşturmaz.
- Paket kanıtı `work/qa/package/result.json` ve `packaged-native.png`; sistem kurulumu yapılmadı. openSUSE Tumbleweed üzerindeki yerel paket ve izole açılış doğrulanmıştır; Leap ve farklı GPU'lar bu koşunun kapsamı değildir.

## Kanal geçişi

Aynı makine, gerçek uygulama teması, yerel 640 × 360 MPEG-2 kırmızı/mavi görüntü; dört ısınma ve yirmi ölçümlü geçiş. Süre `MainWindow.play()` çağrısından hedef framebuffer rengine kadardır. İki koşu ardışık ve başka test oynatıcıları çalışmadan yapıldı; her ikisinde `wayland`, `DISPLAY_present: false` ve tek backend doğrulandı.

| Ölçüm | Değişmemiş 0.3.0 (`e684f21`, `f5b7db9` ile aynı kaynak) | Birleşik 0.4.0 kodu (`c692991`) |
|---|---:|---:|
| Ortanca | 63,44 ms | 62,48 ms |
| p95 | 65,24 ms | 63,49 ms |
| En yüksek | 66,20 ms | 63,50 ms |

Bu eş koşullu fixture'da ek gecikme görülmedi. Ölçüm internet sağlayıcısının gecikmesini veya bütün masaüstü yüklerini kapsamaz; önceki kayıtlardaki farklı koşularla hızlanma iddiası kurulmaz.

## İş akışı ve kanıtlar

#19 sürüm takibi altında #22 → PR #24, #20 → PR #25, #23 → PR #26 ve #21 → PR #27 ayrı branch/test/bağımsız inceleme ile birleştirildi. Bütün özgün testler ve commit geçmişi korunur. Kişisel hesap, playlist, DB, logo cache, venv ve build çıktıları Git dışında kalır. GitHub CI henüz yoktur; belirtilen doğrulamalar bu makinede çalıştırılmıştır.

Ham sonuçlar release çalışma ağacının `work/qa/integrated-full.log`, `zapping-before.json` ve `zapping-after.json` dosyalarındadır; repoya veya kaynak dağıtımına eklenmez. Tekrar komutları önceki kayıttaki Test ve Paket bölümlerinde bulunur.

---

# Luna IPTV 0.3.0 doğrulama kaydı

2026-09-05 · openSUSE Tumbleweed · GNOME Wayland · Python 3.13 · Qt/PySide6 6.11.2. Tek libmpv oynatıcısı, aynı QOpenGLWidget ve mevcut donanım çözümleme yolu korunur; yeni çalışma zamanı bağımlılığı yoktur.

## Sonuçlar

- `env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh`: **205 test geçti, 46,06 saniye**, `-W error`; Ruff lint/format temiz. Yerel video/HLS probe ve tam pencere smoke akışı da başarılı.
- İlk sürümün 45 testini içeren sekiz dosyadaki bütün özgün test fonksiyonları ve yardımcılar, `f74c6695dc79d5d15cf9832b38f4d4e0f4322a32` ile AST düzeyinde aynı. `test_player.py` yalnız async komut future sözleşmesini sınayan yeni bir test içerir.
- Mevcut logo FIFO testindeki yazıcı, okuyucu FIFO'yu doğru biçimde reddedip kapattığında oluşabilen `BrokenPipeError` durumunu artık beklenen kapanış olarak ele alır. FIFO'nun reddedilmesi assertion'ı korunur; FIFO kabul eden geçici mutasyonla testin hâlâ başarısız olduğu doğrulandı.
- Yeni taşıma testleri: ±5 saniye exact seek, iki yönde 2/4/8/16× tarama, ters yön, sınırda durma, önceki pause durumunu koruma, normal oynatmaya dönüş, yeni kaynak/stop/error/close temizliği, canlı/kısmi/bilinmeyen süre için yetenek sınırları. Gerçek yerel videoda düğmeler, kayıtlı kısayollar ve hızlı çıkış/yeniden giriş ayrıca sınanır.
- Tarama, monotonic saatle hesaplanan mutlak keyframe konumlarına en fazla 500 ms'de bir gider. Gösterilen hız hedef zaman çizgisi taramasıdır; kesintisiz 16× decode veya ağ performansı garantisi değildir. Pause bildirimi komut onayı sayılmaz: mpv bildirimleri birleştirebilir. Güncel async future tamamlanmadan tarama başlamaz; eski ve başarısız sonuçlar ile senkron tamamlanan future yolları testlidir.
- Native tam ekranda video ve pencere **2304 × 1296 mantıksal piksel**, video başlangıcı **(0, 0)**; aynı parent, GL context ve Player korunur. Normal/minimum pencere, overlay ve gizlenmiş kontroller görsel olarak incelendi. Normal düzene dönüş, mouse/klavye/menü/slider etkileşimi, 2,5 saniye idle, cursor geri dönüşü ve stop sonrası bilgi panelinin kapalı kalması sınanır.
- Qt Wayland testinde fiziksel imleç taşıma/focus zorlaması yapılmaz. Mouse hareketi gerçek video widget'ına Qt olayı olarak gönderilir; kayıtlı QShortcut testi yalnız test uygulamasının mantıksal active window durumunu hazırlar. Fiziksel masaüstü girdisi test edilmiş gibi sunulmaz.
- Önceki altyazı/GL_BLEND regresyonları, yerel ve localhost HLS görüntü/ses/seek/mute/HTTP başlıkları, M3U/XMLTV, favori, resume, kalıcı veri ve düzenli kapanış testleri de geçer. Gerçek sağlayıcı hesabı veya kişisel kütüphane otomatik testlerde kullanılmaz.

## Kanal geçişi karşılaştırması

Aynı uygulama teması, yerel 640 × 360 MPEG-2 renkleri, dört ısınma ve yirmi ölçümlü geçiş; `MainWindow.play()` çağrısından doğru framebuffer rengine kadar. Her koşuda native Wayland ve tek backend doğrulanır.

| Eş koşullarda ölçüm | Değişmemiş 0.2.1 (`3a843df`) | 0.3.0 |
|---|---:|---:|
| Median | 444,89 ms | 445,78 ms |
| p95 | 447,39 ms | 447,37 ms |

Daha erken bir 0.2.1 koşusu 62,68 / 63,57 ms verdi. Sonraki koşullarda aynı değişmemiş sürüm de yaklaşık 445 ms verdiği için erken ölçüm, sonraki sürümün doğrudan kontrolü sayılmaz. Eş koşullu sonuçlarda yeni kodun belirgin bir ek gecikmesi görülmedi. Profilde 24 geçiş boyunca taşıma UI güncellemelerinin toplamı 1,61 ms, tam ekran event filter toplamı 3,22 ms idi. Bu ölçüm internet sağlayıcısının gecikmesini veya bütün masaüstü yüklerini kapsamaz.

---

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

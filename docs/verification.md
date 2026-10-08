# Luna IPTV 0.8.0 · Gece Ayı arayüzü

2026-10-01 · görünüm yenilemesi. Davranış kodu, veri şeması ve çalışma zamanı bağımlılıkları değişmedi; yeni modüller `icons.py` (SVG çizgi ikonlar, QtSvg ile önbellekli çizim) ve `motion.py` (ikon düğmesi, kayan menü göstergesi, karşılama gökyüzü).

- Düğmeler metin durumlarını korur (`▶/Ⅱ`, `Ses/Sessiz`, `☆/★`, `Mini/Geri dön`); ikon metinden türetilir, bu yüzden davranış kodu ve erişilebilir adlar değişmedi.
- Masaüstü platform teması sınıf bazlı yazı tipi atadığı için `QApplication.setFont()` QLabel/QPushButton'ı değiştirmiyordu; arayüz yazı tipi stil sayfasıyla verilir.
- Performans sınırı test edildi: karşılama gökyüzü 20 fps zamanlayıcısı yalnız görünürken çalışır, yayın açılınca durur (`test_welcome_sky_animates_only_while_visible`). Düğme animasyonları 160 ms, menü göstergesi 260 ms, olay tetiklidir.
- Bulunan hata: mpv yalnız değişen pause değerini bildirdiği için yeni dosya oynarken oynat düğmesi "▶" kalıyordu; yükleme anında durum yazılır ve test edilir.
- Mini oynatıcıda en dar genişlikte süre etiketi 90 px'ten daralmaz; hız düğmesi Mini'de gizlenir.
- Görsel doğrulama: boş kütüphane/karşılama, seçili kanal ile oynatma, Mini, film kartı, kaynak ekleme ve devam penceresi Wayland üzerinde ekran görüntüsüyle incelendi.
- Tam paket: `env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh` → **461 geçti / 63,05 sn**, Ruff lint/format başarılı, probe ve smoke `wayland`, `success:true`.

# Luna IPTV 0.9.0 · Logo Duvarı

2026-10-01 · kanal listesi geniş logolu kart ızgarasına, kenar çubuğu ikon menüsüne, oynatıcı sağ panele taşındı. Veri şeması ve bağımlılıklar değişmedi.

- Kartlar `CardGrid` (IconMode `QListView`) ile sanal çizilir; büyük kataloglarda yalnız görünen kartlar boyanır. Logo görünür alan denetleyicisi ızgarada boşluğa denk gelmemek için kartın içinden yoklar; kaydırılmış ızgarada ilk satırdan tarama yapılmaz.
- Logolar kart sahnesi için 240×96 sınırında hazırlanır (eskiden 76×76).
- `GuideIndex`: kanal başına başlangıca göre sıralı programlar ve ikili arama; kart başına `now()` çağrısı rehberin tamamını taramaz. Sağ panel sıradaki dört programı aynı dizinden okur.
- Çökme: oynatma sürerken kapatmada `release_render_context` içinde `render.update_cb = None`, python-mpv'nin eski ctypes geri çağrı sarmalayıcısını libmpv'nin "vo" iş parçacığı onu çağırırken serbest bırakmasına yol açıyordu (gdb: SIGSEGV "vo" iş parçacığında `_ctypes` içinde, ana iş parçacığı `mpv_render_context_set_update_callback` kilidinde). Eski sarmalayıcı `free()` dönene kadar tutulur. Aynı kapatma senaryosu: düzeltmeden önce yeni arayüzle 5/10, son commit'teki kodla 1/10 çökme; düzeltmeden sonra 0/20 ve 0/10.
- Tam paket: **464 geçti**, Ruff lint/format, probe ve Wayland smoke `success:true`.

# Luna IPTV 0.10.0 · afiş kartları ve detay kartı

- Afiş kartları: `CardGrid.set_poster_mode` bölüme göre geçer; hücre yüksekliği genişlikle 2:3 korunarak hesaplanır. Görünür alan denetleyicisi film/dizi görsellerini afiş önbelleğinden (240×360), kanal logolarını logo önbelleğinden ister. Sütun uydurma artık görünüm alanının kendi boyut olayında yapılır (pencere büyütülünce sütun eklenmiyordu).
- Detay kartı yeniden düzenlendi; tüm mevcut parça adları ve sekme sırası korundu. Oynat/Favori, en küçük kart boyutunda (540×320) görünür kalma kuralı yüzünden sabit alt çubukta. Afiş yuvarlak köşeli çizildiği için testler afişi orta pikselinden doğrular.
- Türkçe büyük harf: kategori etiketinde `i → İ` dönüşümü (`str.upper()` noktasız I üretiyordu).

# Luna IPTV 0.11.0 · IMDb'de bul

- `imdb.find`: `v3.sg.media-imdb.com/suggestion` yanıtında başlık anahtarı (aksan/noktalama duyarsız), tür (film: movie/tvMovie/video; dizi: tvSeries/tvMiniSeries) ve yıl (±1) birlikte tek bir kayda uyarsa başlık sayfası; birden çok, sıfır eşleşme veya hata → `imdb.com/find` arama sayfası. Canlı deneme: Aquaman (2018), Coco (2017), 1883, Bosch: Legacy, Downton Abbey doğrudan; "Alita: Savaş Meleği", "Dokuz Kusursuz Yabancı" arama sayfası.
- Tam ekran: 0.9.0'daki 16:9 oran kuralı tam ekranda da geçerliydi; 16:10 ekranda video pencereyi doldurmuyordu (`test_fullscreen_button_idle_hides_and_mouse_restores_controls` ara sıra düşüyordu). Kural tam ekranda kapatıldı.

# Luna IPTV 0.12.2 · Codex inceleme düzeltmeleri

PR #42 birleştikten sonra `codex exec` (gpt-6-astra, salt okunur) ile `b286310..780f923` incelendi; üç bulgu önce başarısız testle doğrulandı, sonra düzeltildi:
- `untracked` oynatma yolunda pause yalnız `player_property` ile gelir; uyku engeli orada da güncellenir (`test_untracked_pause_also_releases_inhibit`).
- Paylaşılan afiş önbelleğinde `request_visible` kuyruğu temizlerken detay kartının bekleyen isteğini siliyordu; açık istekler (`request_logo`) tamamlanana kadar kuyruğun başında tutulur (`test_explicit_request_survives_visible_queue_replacement`).
- Hücreler görünüm genişliğini tam doldurunca Qt son hücreyi alt satıra taşıyordu (600 px'te 2 yerine 1 sütun); hücre genişliği bir piksel kısaltıldı, test gerçek `visualRect` satırlarını doğrular.

# Luna IPTV 0.13.0 · günlük kolaylıklar

- MPRIS modülü (`mpris.py`) Codex'e (gpt-6-astra) yazdırıldı; Codex'in korumalı ortamı D-Bus'a erişemediği için canlı testleri atlanmıştı. Gerçek oturum veri yolunda `busctl` ile dışarıdan doğrulandı: PlaybackStatus `s`, Position `x`, Metadata `a{sv}` (trackid `o`, artist `as`, length `x`), Seek `x`, SetPosition `ox`, Seeked `x`; Next/Seek komutları denetleyiciye ulaşır. PySide6'nın `a{sv}` okuyamaması yüzünden düşen 5 test, okumayı `busctl` ile yapacak şekilde düzeltildi (24 geçti).
- Int64 için Qt'nin `QDBusArgument::operator<<(qlonglong)` sembolü ctypes ile çağrılır; sembol bulunamazsa `mpris:length` atlanır, diğer alanlar yayımlanır.
- Pencere testlerinde gerçek MPRIS yerine kayıt tutan bir sahte servis kullanılır (`tests/conftest.py`); testler masaüstü medya kontrollerinde görünmez.
- `scripts/test-quiet.sh` (offscreen, nice/ionice): **487 geçti, 5 atlandı**, 3 yerel video bağımlı hesap testi hariç; gerçek video/pencere yöneticisi testleri `scripts/test.sh` ile ayrıca çalıştırılır.

# Luna IPTV 0.14.0 · gezinme ve düzen

- Paralel çalışma: bölüm listesi, ayarlar ve favori klasörleri Codex'e (gpt-6-astra) ayrı worktree'lerde yazdırıldı. Codex'in korumalı ortamı git'e yazamadığı ve yerel HTTP sunucusu açan 33 testi çalıştıramadığı için her kopyada tam sessiz paket burada çalıştırıldı (509, 514, 554 geçti), kod satır satır incelendi, commit ve birleştirme burada yapıldı. Tek çakışma `icons.py` sonundaki iki ikon eklemesiydi; ikonlar sözlüğe taşındı.
- Kategori/arama: Favoriler ve Geçmiş'te arama kendi listesinde kalır (`test_recent_filters_preserve_newest_first`, `test_search_composes_with_source_group_favorite_and_recent` bu tasarımı korudu).
- Codex'in bölüm listesi işi, dizi detayları yüklenince katalog sıfırlamasının izleme ilerlemesini sildiği bir 0.13 hatasını da düzeltti.
- `scripts/test-quiet.sh`: **554 geçti**, 5 atlandı. Ayarlar, tercih kaydetme mantığına (`TrackPreferences.begin(persist=...)`) dokunduğu için `tests/test_preferences_native.py` ve `tests/test_preplay_native.py` gerçek ekranda ayrıca çalıştırılmalı.
- Görsel kontrol (offscreen ekran görüntüsü): kategori düğmeleri, favori klasörleri, bölüm listesi, ayarlar. Favorilerde kaynak seçici satırı kaplıyordu; genişliği sınırlandı.

# Luna IPTV 0.15.0 · rehber, hatırlatıcı, yedekleme

- Rehber burada yazıldı; yedekleme ve hatırlatıcı Codex'e (gpt-6-astra) ayrı worktree'lerde paralel yazdırıldı, her biri burada tam sessiz paketle sınandı (619, 580 geçti) ve incelendi.
- İncelemede düzeltilen: hatırlatıcının "kaç dakika önce" değeri `app_settings` tablosuna `reminder_lead:<id>` anahtarıyla yazılıyordu (görev tanımındaki tabloda sütun yoktu); bu ayarları kirletir ve yedeğe sızardı. Değer `reminders.lead_minutes` sütununa taşındı; testler ayarların temiz kaldığını da doğrular.
- Birleştirmede git çakışma göstermedi ama rehberin yer tutucu `remind_programme`/`open_reminders` yöntemleri hatırlatıcının gerçek yöntemleriyle aynı adı taşıyordu (Python sessizce üstüne yazar). Yer tutucular kaldırıldı; rehber hatırlatıcıları (kanal, başlangıç zaman damgası) ile eşleştirir. Uçtan uca test: rehberden hatırlatıcı kurulur, kart "Hatırlatıcı kurulu" gösterir.
- `GuideIndex.between()` ikili aramayla yalnız görünen zaman penceresini döndürür; rehber yalnız ekrandaki satırları boyar.
- `scripts/test-quiet.sh`: **650 geçti**, 5 atlandı. Görsel kontrol (offscreen): rehber, hatırlatıcılar ve yedekleme pencereleri.

# Luna IPTV 0.16.0 · profiller ve ebeveyn denetimi

- Veri katmanı (profil başına favori/klasör/geçmiş/hatırlatıcı, tek işlemde tablo yeniden kurma göçü, PIN özeti, kategori/yayın kilitleri) Codex'e (gpt-6-astra) ayrı worktree'de, arayüzle aynı anda yazdırıldı; API önceden sabitlendi. Eski şemayla kurulan veritabanının profile 1'e kayıpsız taşındığı ve ikinci açılışta değişmediği test edilir.
- PIN her seferinde sorulur; testler aynı kanalın iki kez açılmasında iki kez sorulduğunu, kilitsiz kanalda sorulmadığını, ayarlar/kaynak/yedek/profil/denetim kapılarını, beş yanlışta 30 saniyelik beklemeyi ve beklemenin pencere yeniden açılınca sıfırlanmadığını doğrular.
- Çocuk profilinde kilitli içerik listede, aramada ve rehberde görünmez; kanal değiştirme kilitlileri atlar; bölümler dizi kartı PIN'le açıldıktan sonra yeniden sormaz.
- Kategori düğmelerinin `main`'de de sıkışıp kırpıldığı (ör. "3elgese") offscreen görüntüde fark edildi; sığmayan düğmeler artık listeye geçer.
- `scripts/test-quiet.sh`: **760 geçti**, 5 atlandı (Codex incelemesi düzeltmeleriyle). Görsel kontrol (offscreen): ana pencere (kilit rozetleri, profil simgesi), "Kim izliyor?", PIN, ebeveyn denetimi, profil düzenleyici.

# Luna IPTV 0.17.0 · ana sayfa ve izleme kolaylıkları

- Ana sayfa Codex'e (gpt-6-astra) ayrı worktree'de, uyku zamanlayıcısı / sonraki bölüm / kanal numarası ile aynı anda yazdırıldı; birleştirme çakışmasızdı. İncelemede: şeritler kimlik listesi değişmedikçe yenilenmez (oynatma sırasında ilerleme kaydı kaydırmayı sıfırlamaz), kapak resimleri her şerit için ayrı görünür alanla istenir.
- Testler: ana sayfa şeritleri ve sıraları, selam saatleri, çocuk profili, kart açma yolları; bölüm sonu geri sayımı, durdurma/iptalde başlamaması, otomatik oynatma kapalıyken öneri, "bitince dur" zamanlayıcısının sonraki bölümü de engellemesi, uyku sonunda durma, rakamla kanal açma.
- GitHub Actions bu sürümle başladı: sessiz testler Ubuntu'da; pencere/video testleri ilk denemede ffmpeg yokluğundan atlandı, sonra Ubuntu'nun libmpv 0.37'sinde `loadfile` dizin argümanı olmadığından oynatma başlamadı; openSUSE Tumbleweed konteynerine taşındı.
- `scripts/test-quiet.sh`: **795 geçti**, 5 atlandı. Görsel kontrol (offscreen): ana sayfa, sonraki bölüm bandı ve uyku düğmesi.

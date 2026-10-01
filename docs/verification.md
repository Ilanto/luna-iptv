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

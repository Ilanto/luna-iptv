# Luna IPTV 0.4.0 — bağlantılar ve günlük kullanım

Kullanıcı 2026-09-05 tarihinde hesap düzenleme/bağlantı dayanıklılığı ile mini player, ses/altyazı tercihlerini hatırlama, devam et/baştan başlat, geçmiş temizleme ve küçük UI polish uygulamasını onayladı. Kayıtlı ilerlemesi olan film/bölüm seçilince önce **Devam et / Baştan başlat** seçilecek; canlı zapping doğrudan kalır. Issue #19, alt işler #20–23.

## Ortak koşullar

- Başlangıç: f5b7db98a4ab65faca8532b94e16cde280bef1f1, 0.3.0; ilk 45 dahil mevcut 205 test korunur.
- Python >=3.11, PySide6 >=6.8,<6.12, python-mpv >=1.0.8,<2, tek libmpv/QOpenGLWidget; native GNOME Wayland, değişmeyen GL context/parent ve hızlı kanal geçişi. Yeni runtime bağımlılığı eklenmez.
- Sıcak #1d2021, #e889a8, #f8e7ec ve Hurmit ilkeleri korunur. Polish yalnız seçili parça işareti, tutarlı adlar/tooltips/erişilebilirlik ve kompakt kullanım için gerekli boşlukları kapsar.
- Her iş kendi issue/branch/PR/test/inceleme akışını izler. Kullanıcı hazır PR merge edilmesini zaten onayladı. Root birleştirme, çakışma çözümü, release/test/RPM ve uygulamayı açma işini koordine eder.
- Gerçek sağlayıcı/kütüphane testlerde kullanılmaz. Geçici SQLite ve üretilmiş video/localhost sunucular; sırlar/URL'ler hata ve loglara girmez; venv/cache/build Git'e girmez.

## #20 Kaynak bağlantısını düzenleme

Mevcut M3U adresi, Xtream sunucu/kullanıcı/şifre ve doğrudan kaynak düzenlenebilir; tür değiştirilmez. Aday kaynak arka planda doğrulanır, katalog hazır olmadan kaynak/katalog/snapshot tek transaction ile değiştirilmez. Hata, iptal ve eski cevap önceki durumu korur. Kaynak kimliği sabit kalır. Xtream sunucu değişse bile eşleşen sağlayıcı yayın kimliklerinin favori/ilerlemesi sonraki refresh ve episode yüklemelerinde de korunur; cache edilmiş bölümler eski credential URL'si kullanmaz. Oynayan aynı yayının mevcut oturumu kesilmez; sonraki açılış yeni URL'yi kullanır. Kaynak menüsünde isteğe bağlı bağlantı kontrolü, son kontrol zamanı ve anlaşılır durum gösterilir; bu kontrol video akışı açmaz ve katalog yenilemekle karıştırılmaz.

## #21 Bağlantı dayanıklılığı

Canlı yayın hatası veya uzun bağlantı/buffer beklemesinde tek backend ile en fazla üç yeniden deneme; 1/2/4 saniye bekleme, her denemeye zaman sınırı, bütçe yalnız kararlı oynatma sonrasında sıfırlanır. Yeni kanal, stop, silme/düzenleme ve close eski timer/cevapları geçersiz kılar. VOD bitişi veya kullanıcı pause'u hata sayılmaz; VOD manuel deneme mevcut ilerlemeyi korur. Canlı doğal EOF toparlanabilir; eski dosyanın EOF'u yeni kanalı yeniden açamaz. Bağlanıyor/buffer/yeniden deneniyor/başarısız bilgisi ve iptal erişilebilir olsun; gecikme/son durum gerçek gözleme dayansın. Genel UI/seek hatası bağlantı kopması gibi ele alınmaz.

## #22 Tercihler, devam et ve geçmiş

Ses/altyazı seçimi kaynak bazında kalıcıdır; mpv numeric track ID saklanmaz. Dil/başlık gibi anlamlı metadata eşleştirilir; aynı tercihin bulunmadığı videoda varsayılan seçime dönülür. Açık Kapalı tercihi kalıcıdır; sağlayıcı varsayılanlarına dönme ve hatırlamayı kapatma/sıfırlama vardır. Yüklenme ve otomatik seçim kullanıcı tercihini üzerine yazmaz.

Kütüphaneden kayıtlı VOD seçilince Devam et (zaman etiketi) / Baştan başlat / Vazgeç sunulur. İptalde mevcut oynatma/veri değişmez. Baştan başlat kayıtlı konumu doğru sıfırlar; bitişe çok yakın veya ilk birkaç saniyedeki kayıt doğrudan başlar. Aynı açık yayında stop→Play ve otomatik recovery tekrar soru açmaz; kullanıcı yeni bir kütüphane seçimi yaptığında karar verilir. Oynatıcı menüsünde ayrıca Baştan başlat erişilebilir olur.

Son izlenenler görünümünde geçmiş temizleme onaylıdır; ayrıca devam konumlarını sıfırlama seçeneği vardır (varsayılan kapalı). Favoriler ve kaynaklar korunur. Temizleme sonrasında oynayan yayın timer/close/recovery ile hemen geri eklenmez; yeni açık kullanıcı oynatma seçimi kaydı yeniden başlatır.

## #23 Mini player

Aynı native pencere ve mevcut video widget'ı kompakt moda geçer; ikinci player veya widget reparent yok. Normal pencere geometrisi, minimum boyut ve görünürlük geri alınır. Mini modda video, anlaşılır dönüş düğmesi, oynat/pause, ±5sn, mute/ses ve temel durum bulunur; büyük liste/rehber/başlık gizlenir. Tam ekranla geçişler kontrollüdür; F/Esc/dönüş/close ve yeni kaynakta çalışır. GNOME Wayland'de garantisi olmayan always-on-top iddiası veya XWayland gereği eklenmez.

## Teslim

Bütünleşik testler, gerçek native yerel/HLS, parça tercihi ve mini/fullscreen görüntü testleri, eş koşullu kanal geçiş ölçümü, inceleme ve openSUSE 0.4.0 RPM. Paket kaynak baytları ve extracted native launch doğrulanır. Geliştirme mevcut kullanıcı uygulamasını veya verisini değiştiren otomatik test yapmaz.

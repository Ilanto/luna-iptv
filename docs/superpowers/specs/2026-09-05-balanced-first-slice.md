# Luna IPTV — dengeli günlük kullanım, ilk dilim

Kullanıcı 2026-09-05 tarihinde bu kapsamı açıkça uygulama için onayladı:
kaynak yeniden adlandırma, son izlenen sırası, arama optimizasyonu, kanal
logoları ve cache, gerçek yayın bilgi paneli, buffer göstergesi ve Xtream
hesap durum bilgileri. Mevcut 45 test korunacak ve her davranışa test eklenecek.

## Kabul ölçütleri

- Tek Qt Widgets / QOpenGLWidget / libmpv oynatıcı korunur. Native GNOME
  Wayland varsayılandır; XWayland zorunluluğu ve ikinci decoder eklenmez.
- Başlangıç commit'i f74c6695dc79d5d15cf9832b38f4d4e0f4322a32, private
  Ilanto/luna-iptv deposunda main ve luna-client üzerinden doğrulanmıştır.
- Her bağımsız iş issue, feature/fix branch, PR, test ve merge akışını izler.
  Push ve testlerden sonra hazır PR'ı merge etme kullanıcı tarafından onaylıdır.
- Gerçek kullanıcı veritabanı veya sağlayıcı hesabı geliştirme testlerinde
  kullanılmaz. Testler geçici dizinler ve sentetik/localhost yayınları kullanır.
- Yalnız kaynak adı değişir; kaynak/kanal kimliği, erişim bilgileri, favoriler,
  EPG ve ilerleme korunur. İptal ve boş ad veriyi değiştirmez.
- Son izlenenler en yeni kayıttan eskiye gösterilir; filtreler bu sırayı korur.
  Diğer kütüphane görünümleri sağlayıcının katalog sırasını korur.
- Türkçe karakter toleranslı arama korunur. Aynı kanal metni her tuşta yeniden
  normalize edilmez. 10k/50k/100k sentetik ölçümle önce/sonra raporlanır.
- M3U tvg-logo ve Xtream stream_icon/cover, göreli adresleriyle desteklenir.
  Görünür satırlar öncelikli indirilir; paint içinde ağ/disk/çözümleme yapılmaz.
  Bellek/disk kotası, süreli cache, başarısız URL bekleme süresi ve baş harf
  fallback'i bulunur. URL'ler/hesap bilgileri loglanmaz, medya başlıkları CDN'ye
  otomatik aktarılmaz. Uzak liste yerel dosya logosu okuyamaz.
- Bilgi paneli MPV'nin gerçek video boyutu, kalite etiketi, video/ses codec'i,
  FPS, ses düzeni, mümkünse bitrate ve HDR/SDR bilgisini gösterir. Eksik değer
  0 veya sahte kalite olarak sunulmaz. 1080i/1080p ayrımı korunur; kaynak HDR
  bilgisinin ekranın HDR çıkışı olmadığı açıklanır. Kanal değişimi eski bilgiyi
  temizler. Buffer göstergesi toplam yayın indirme yüzdesi gibi sunulmaz.
- Xtream profili yalnız gereken alanlardan oluşur. auth ile status ayrılır;
  created_at hesap oluşturulma tarihidir, üyelik yenilenme tarihi değildir.
  Bitiş, kalan gün/yaklaşık ay, durum, aktif/maksimum bağlantılar ve son kontrol
  gösterilir. Eksik/bozuk bilgiler bilinmiyor olur; ağ hatası süre dolumu değildir.
  Katalog yenilemeden hesap bilgisi yenilenebilir; eski cevap silinen kaynağı
  geri getirmez. Ham profil parolası/tokenı yeni metadata'ya yazılmaz.
- Luna yüzeyi #1d2021, vurgu #e889a8, metin #f8e7ec; Hurmit Propo/Mono ilkesi,
  sakin masaüstü arayüzü ve kontrollü yuvarlatma korunur.
- Bu dilimde hesap erişim bilgisi düzenleme, PiP, kayıt, timeshift, profil,
  ebeveyn kontrolü veya sağlayıcı kategorisini otomatik yeniden sınıflandırma yok.

## Teslim

Birleşik kaynak için tam testler, yerel/HLS native Wayland oynatma, GUI smoke,
kanal geçişi ölçümü, güvenli dosya kontrolü ve güncellenmiş openSUSE RPM doğrulanır.
Build/cache/venv/kişisel veri Git'e girmez. Son rapor PR'ları, sürümü, paket yolunu,
test sayısını ve ölçümlerin sınırlarını açıklar.

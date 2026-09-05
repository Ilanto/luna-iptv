# Luna IPTV yol haritası

Bu belge 0.4.0 sonrasındaki ürün yönünü düzenler. Tarih, sürüm sözü veya otomatik
uygulama yetkisi vermez. Her gelecek işi başlamadan önce ayrı kapsam, issue, kabul
ölçütü ve kullanıcı onayı alır.

## Durum anahtarı

| Durum | Anlamı |
|---|---|
| Teslim | Mevcut sürümde çalışıyor ve doğrulandı. |
| Kısmi | Temel davranış var; aşağıdaki genişletme henüz yok. |
| Gelecek | Uygulanmadı ve henüz bir sürüme seçilmedi. |

## Bugünkü temel

| Alan | Durum | Mevcut kapsam |
|---|---|---|
| Kaynaklar | Teslim | M3U, Xtream ve doğrudan kaynak ekleme; yeniden adlandırma, bağlantı düzenleme, yenileme, sağlık kontrolü ve kaldırma. |
| Kütüphane | Teslim | Canlı, film, dizi, kategori, düz favoriler, son izlenenler ve Türkçe/aksan duyarsız arama. |
| Rehber | Kısmi | XMLTV içe alma ve oynayan kanal için şimdi/sıradaki bilgisi var; gelişmiş gezinme ve arama yok. |
| Oynatma | Teslim | Tek native libmpv/Qt video yüzeyi, seek/tarama, tam ekran, Mini, medya bilgisi ve sınırlı canlı yeniden bağlanma. |
| Kişisel tercihler | Teslim | Kaynak bazlı ses/altyazı tercihi, devam et/baştan başlat ve geçmiş temizleme. |
| Dağıtım | Kısmi | Doğrulanmış openSUSE RPM üretimi var; uygulama içi güncelleme ve yayımlanmış güncelleme kanalı yok. |

## Sıradaki ürün alanları

Bu sıra günlük faydayı önceleyen çalışma sırasıdır; hiçbir satır seçilmiş iş veya
taahhüt değildir.

| Sıra | Alan | Durum | Hedef kapsam | Ön koşul ve sınır |
|---:|---|---|---|---|
| 1 | Gelişmiş EPG | Gelecek | Gün/saat içinde gezinme, program arama ve ayrıntı; stop zamanı eksik kayıtlar için açık davranış; kaynak ve zaman dilimi durumu. | Mevcut XMLTV parser/cache sınırları korunmalı. Catch-up bağlantısı bu temel ve sağlayıcı yetenek modeli oluşmadan eklenmemeli. |
| 2 | Favori klasörleri | Gelecek | Kullanıcının adlandırdığı klasörler, klasör sırası ve kanalı klasörler arasında yönetme. | Mevcut favoriler kayıpsız taşınmalı; kanal kimliği yenilemeler boyunca korunmalı. |
| 3 | Kategori denetimi | Gelecek | Kategorileri gizleme, yeniden sıralama ve kullanıcı tercihlerini kaynak bazında saklama. | Sağlayıcı verisi değiştiğinde kullanıcı düzeni bozulmamalı. Sağlayıcı türünü veya kategorisini otomatik yeniden sınıflandırma bu yol haritasında yoktur. |
| 4 | Arama ve filtreler | Gelecek | Arama sonuçlarını kaynak, içerik türü, kategori ve kişisel listelerle daha rahat daraltma; filtre durumunu anlaşılır biçimde gösterme. | Büyük katalog arama hızını ve birleşen filtrelerin doğruluğunu korumalı. |
| 5 | Linux masaüstü uyumu | Gelecek | MPRIS durumu/metadata, medya tuşları, oynatma sırasında ekran uyku engeli ve kullanıcının açtığı isteğe bağlı bildirimler. | D-Bus yokken sessizce temel davranışa dönmeli; pause/stop/close sonrası inhibit mutlaka bırakılmalı. Bildirimler yayın adresi veya hesap bilgisi taşımamalı. |
| 6 | Yedekleme ve geri yükleme | Gelecek | Kaynaklar, favoriler, klasörler, kategori düzeni, tercihler ve istenirse geçmiş için sürümlü dışa aktarma/içe aktarma. | Erişim bilgilerinin dahil edilmesi açık kullanıcı seçimi olmalı. İçe aktarma önce doğrulanmalı, mevcut veri atomik işlem olmadan değiştirilmemeli. |
| 7 | Profiller ve ebeveyn denetimi | Gelecek | Ayrı profil tercihleri/geçmişi ve tanımlı içerik kısıtları. | Profil sahipliği ve veri göçü önce tanımlanmalı. PIN erişim denetimidir; veri şifreleme vaadi olarak sunulmamalı. |

## Güç kullanıcıları için daha sonraki adaylar

| Alan | Durum | Bağımlılık ve ürün kararı |
|---|---|---|
| Kayıt | Gelecek | Hedef dizin, adlandırma, disk kotası, yarım kayıt, kapanış ve kanal değiştirme davranışı belirlenmeli. |
| Catch-up (sağlayıcı arşivi) | Gelecek | Gelişmiş EPG, kararlı program kimliği, zaman dilimi kuralları ve sağlayıcının arşiv yeteneğini keşfetme gerekir. |
| Yerel time-shift | Gelecek | Sınırlı yerel yayın tamponu, disk kotası/temizlik, seek ve kesinti kuralları; kanal değiştirme, stop ve çökme sonrası davranış gerekir. EPG veya sağlayıcı arşivine bağlı değildir; her yayının sarılabileceği vaat edilmez. |
| Gerçek PiP | Gelecek | Mevcut Mini tam PiP değildir. Wayland pencere davranışı ve her zaman üstte kalma sınırı açıkça tanımlanmalı. |
| Çoklu ekran | Gelecek | Bugünkü tek oynatıcı/video bağlamı ilkesini değiştirir; decoder, ağ ve GPU bütçesi ayrıca tasarlanmalı. |
| DRM | Kapsam dışı | Yalnız sağlayıcı, lisans, CDM/libmpv ve paketlenebilirlik fizibilitesi araştırılabilir; teslim hedefi değildir. |

## Güncelleme yaklaşımı

Güncelleme, uygulamanın kendi dosyalarının üzerine yazmasıyla yapılmaz. Gelecekteki
akış yayımlanmış sürümü ve doğrulanabilir RPM'i gösterir; indirme/kurulum sistem paket
yöneticisine bırakılır. İmza, checksum, sürüm notu ve geri dönüş yolu tanımlanmadan
otomatik güncelleme sunulmaz.

## Teslim ve doğrulama ritmi

Her onaylı işte davranışa odaklı testler ve ilgili modüllerin kontrolleri çalışır.
Birleştirme öncesinde tek bir tam bütünleşik koşu yapılır; oynatıcı, pencere veya
masaüstü entegrasyonu değişiyorsa ilgili native Wayland davranışları aynı doğrulama
turunda kapsanır. Yeni değişiklik veya başarısızlık yokken aynı pahalı koşular tekrar
çalıştırılmaz. CI eklendiğinde bu ritmi desteklemeli; ürün işlerinin önüne geçen
ayrı bir dev altyapı fazı olmamalıdır.

## Ayrı onay bekleyen tasarım araştırması

UI/UX, tema, hareket ve logo araştırma çıktıları bu yol haritasına dahil değildir.
Kullanıcı belirli bir öneriyi onaylarsa o öneri ayrı issue ve kabul ölçütleriyle
eklenebilir. O zamana kadar mevcut sıcak renk sistemi, erişilebilirlik ve native
Qt/libmpv sınırları korunur.

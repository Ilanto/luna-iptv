# Geliştirme akışı

Önemli geliştirmeler **issue → branch → PR → test → merge** sırasını izler.

1. Çalışmaya başlamadan ilgili açık issue ve PR'ları kontrol edin. Mevcut işi
   devam ettirin; aynı kapsam için yeni bir issue veya PR açmayın.
2. Yeni işin kapsamını, kabul ölçütlerini ve bağımlılıklarını issue üzerinde
   belirtin. Ana branch üzerinde değişiklik biriktirmeyin; issue için ayrı
   bir branch kullanın.
3. Başka çalışmalara ait değişiklikleri koruyun. Commit öncesi diff'i ve
   eklenecek dosyaları inceleyin; yalnız ilgili dosyaları stage edin.
4. PR açıklamasında kullanıcıya etkisini, doğrulamayı ve ilgili issue'yu yazın.
   Kapsam tamamlanıyorsa `Closes #<issue-numarası>` ile ilişkilendirin.
5. İlgili testleri çalıştırın. Oynatıcı veya masaüstü davranışını değiştiren
   PR'larda native Wayland doğrulamasını da tamamlayın:

   ```sh
   env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh
   ```

6. Merge öncesi PR'ın güncel commitini, test/CI sonuçlarını, incelemeleri ve
   çözülmemiş yorumları kontrol edin. Depodaki branch korumalarını ve mevcut
   merge yöntemini izleyin; zorunlu kontrolleri atlamayın.
7. Merge sonrası issue durumunu ve ana branch'i doğrulayın; sonraki işi kendi
   issue ve branch'i üzerinden sürdürün.

## Repoya girmemesi gereken veriler

Gerçek Xtream kullanıcı bilgileri, parolalar, tokenlar, erişim bilgisi taşıyan
URL'ler, kişisel M3U/XMLTV listeleri, SQLite kütüphaneleri, cache, kayıtlar,
yerel yedekler, sanal ortamlar ve build çıktıları Git'e eklenmez.

`.gitignore` yalnız takip edilmeyen dosyaları korur. Commit/push öncesi stage
edilen içerik ayrıca incelenmelidir. Testler sentetik veriler ve yerel HTTP
sunucuları kullanmalı; gerçek hesap dosyaları fixture yapılmamalıdır. İleride
dosya tabanlı fixture gerekirse yalnız incelenmiş sentetik örnekler için dar
bir ignore istisnası eklenmelidir.

RPM ve diğer dağıtım çıktıları kaynak ağacına commit edilmez; GitHub deposu
hazırlandıktan sonra sürüm varlıkları veya CI çıktıları olarak dağıtılabilir.

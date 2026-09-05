# 0.2.1 · Qt/libmpv görüntü durumu

## Hata ve neden

0.2.0'da Alt+Tab sonrasında siyah video ve yatay renkli bozulma bildirildi. Yerel üretim 4K HEVC Main10/PQ video, NVIDIA RTX 5080 (580.178.04), Qt 6.11.2 ve native GNOME Wayland üzerinde aynı çizgili bozulmayı altyazı açarak yeniden üretti. Hata için sağlayıcı bağlantısı ya da pencere odağının değişmesi şart değildi. Altyazıyı tekrar kapatmak mevcut bozulmayı gidermiyordu.

Qt, `paintGL()` öncesinde `GL_BLEND` durumunu açıyor. libmpv bu durumun kapalı olmasını bekliyor; altyazı çiziminden kalan alpha blend fonksiyonu, sonraki video karesini Qt'nin içeriğini korumadığı framebuffer ile karıştırabiliyor. Yerel GL ölçümünde çizim girişinde blending açık, altyazıdan sonra fonksiyonlar `SRC_ALPHA/ONE_MINUS_SRC_ALPHA` idi.

- [Qt 6.11.2 QOpenGLWidget kaynağı](https://github.com/qt/qtbase/blob/v6.11.2/src/openglwidgets/qopenglwidget.cpp)
- [libmpv OpenGL durum sözleşmesi](https://github.com/mpv-player/mpv/blob/v0.41.0/include/mpv/render_gl.h)
- [mpv OpenGL render pass uygulaması](https://github.com/mpv-player/mpv/blob/v0.41.0/video/out/opengl/ra_gl.c)
- [Benzer upstream kullanıcı raporu](https://github.com/mpv-player/mpv/issues/18259)

## Düzeltme

`VideoWidget.paintGL()` içinde mpv çiziminden hemen önce `glDisable(GL_BLEND)` çağrılır. Altyazı için gereken blending'i mpv kendi pass'inde açabilir. Donanım çözümleme, EGL bağlamı, framebuffer seçimi, oynatıcı ömrü ve kanal yükleme yolu değişmez; yeni bağımlılık eklenmez.

Yalıtılmış A/B denemelerinde yalnız aktif texture birimini veya unpack hizalamasını sıfırlamak hatayı gidermedi. Blending'i kapatmak ise NVDEC açıkken temiz görüntü verdi. Geniş QtQuick durum sıfırlamasına veya yazılım decode'a geçmeye gerek kalmadı. `INVALID_ENUM` mesajı tek başına görüntü bozukluğu göstergesi değildir; bu mesaj üzerinden decoder fallback yapılmaz.

## Regresyon doğrulaması

`tests/test_render_state.py`, FFmpeg ile H.264 ve HEVC Main10/PQ renk çubukları ve yerel SRT üretir. Gerçek MainWindow/libmpv ile altyazı açma/kapatma, duraklatma/devam, pencere gizleme/geri getirme, bilgi paneli ve boyut değişiminden sonra framebuffer piksellerini inceler. Sabit renk çubuklarının dikey tutarlılığı ile siyah/tek renk görüntü ayrı ayrı kontrol edilir; aynı Player korunur. FFmpeg ve ilgili encoder yoksa test açıkça atlanır.

Eski kodda HEVC testi altyazı açıldıktan sonra 253/255 kanal farkıyla başarısız oldu; düzeltmeyle iki codec testi de geçti. 4K NVDEC denemesinde fark 90–212 düzeyinden 1–2'ye indi (encode/renk dönüşümü yuvarlaması). Tam test akışında 165 test, yerel/HLS render probe ve gerçek pencere smoke testi geçti; `DISPLAY` kaldırıldı ve Qt platformu `wayland` olarak doğrulandı. Önceden mevcut 163 testin dosyaları ve ilk sürümdeki 45 testi içeren sekiz dosya değişmedi.

Aynı uygulama teması ve sentetik MPEG-2 videolarla 20 kanal geçişi ölçümü: önce median 62,67 ms / p95 64,01 ms; sonra median 62,52 ms / p95 63,29 ms. Bu küçük fark hızlanma iddiası değildir; gözlenen geçiş hızında kayıp yoktur. İnternet sağlayıcısının gecikmesi ölçülmez.

Bu doğrulama yerel medyayı ve Qt pencere geçişlerini kapsar. Kullanıcının fiziksel Alt+Tab tuşları, gerçek sağlayıcı akışı ve diğer GPU/sürücü kombinasyonları ayrı kullanıcı doğrulaması gerektirir. HDR etiketi ekranın HDR çıkışını doğrulamaz.

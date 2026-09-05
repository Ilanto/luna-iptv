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

# Luna IPTV

Linux için özgün, kişisel IPTV istemcisi. Python, Qt 6 ve libmpv kullanır. GNOME Wayland üzerinde Qt'nin OpenGL yüzeyine doğrudan video çizer; XWayland zorunlu değildir.

## openSUSE kurulumu

```bash
sudo zypper install ./dist/luna-iptv-0.1.0-1.noarch.rpm
luna-iptv
```

Dosya adı farklıysa `dist/` içindeki RPM adını kullanın. Paket bağımlılıkları: Python >=3.11, python3-pyside6 >=6.8, python3-python-mpv >=1.0.8 ve libmpv2. PySide6 ve python-mpv üst sınırları pyproject/spec içinde sabittir. RPM yerel geliştirme çıktısıdır, dağıtım deposu imzası içermez. openSUSE Tumbleweed üzerinde üretilir; Leap uyumluluğu ayrıca doğrulanmamıştır. Kurulum yönetici yetkisi gerektirir; geliştirme sırasında sistem paketleri değiştirilmez.

## Kullanım

“Kaynak ekle” ile yerel/uzak M3U, Xtream hesabı veya tek yayın/video dosyası açın. Solda kaynak, içerik türü ve kategori seçin; arayın ve bir yayına tıklayın. Yıldız favoriye ekler. Xtream dizilerinde diziye tıklamak bölümleri açar; sezon seçimi kategori alanından yapılır. Kaynak işlemleri menüsünden listeyi yenileyin veya kaldırın. XMLTV adresini M3U ile birlikte ya da “Rehber ekle” üzerinden bağlayın; kanal eşleştirmesi `tvg-id` ile yapılır. M3U'daki `url-tvg` ve `x-tvg-url` rehberleri otomatik algılanır.

Oynatıcı pause, ses/mute, desteklenen akışlarda seek, ses/altyazı seçimi ve tam ekran içerir. Film/bölüm konumu otomatik kaydedilir; bitişe yakın konumlar yeniden başlatılır. Son izlenenler yerel geçmişten gelir. Canlı yayınlarda seek, akışın sağladığı pencereye bağlıdır.

| Kısayol | İşlem |
|---|---|
| Ctrl+O | Kaynak ekle |
| Ctrl+F | Ara |
| Boşluk | Oynat / duraklat |
| F / Esc | Tam ekrana gir / çık |
| M | Sesi kapat / aç |
| Sol / sağ | Desteklenen akışlarda 10 saniye sar |

Bir dosyayı pencereye bırakabilir veya `luna-iptv liste.m3u` / `luna-iptv video.mkv` çalıştırabilirsiniz. Arama yazarken tek harfli oynatıcı kısayolları devreye girmez.

## Yerel veri ve tasarım

Kütüphane `$XDG_DATA_HOME/luna-iptv` (varsayılan `~/.local/share/luna-iptv`) altında SQLite'tır. Dizin 0700, veritabanı 0600 izinlidir. Kaynak şifreleri ve şifre içerebilen yayın adresleri disk üzerinde ayrıca şifrelenmez. Uygulama bunları günlük mesajlarına yazmaz; telemetri, hesap sunucusu veya bulut senkronizasyonu yoktur. Ayrı test kütüphanesi için `--data-dir /tmp/luna-test` kullanın.

Luna tasarım sistemi: sıcak `#1d2021` yüzey, `#e889a8` vurgu, `#f8e7ec` metin; Hurmit Nerd Font Propo arayüz yazısı ve uygun yerlerde mono yazı ilkesi. Hurmit kurulu değilse sistem sans yazısına döner. Yazı tipi dağıtıma eklenmez. Renk/boşluk kuralları `luna_iptv/theme.py` içinde merkezidir. Özgün hilal/oynat simgesi uygulamaya aittir. Neon, glow ve sürekli animasyon yoktur.

## Kaynaktan çalıştırma

Sistemin geliştirme gereksinimleri:

```bash
sudo zypper install python3 python3-pip python3-pyside6 python3-python-mpv libmpv2
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -e '.[dev]'
./scripts/run-dev.sh
```

`run-dev.sh`, bu geliştirme oturumunda indirilen `work/deps/root/usr/lib64` varsa onu yalnız bu süreç için kullanır. Sistem kurulumunda buna gerek yoktur. Qt, Wayland oturumunda native Wayland'ı seçer. Sorun teşhisinde `QT_QPA_PLATFORM=xcb` elle seçilebilir; uygulama bu seçimi zorlamaz.

## Test ve paket

```bash
./scripts/test.sh
./scripts/build-rpm.sh
```

Medya testleri FFmpeg ile yerel sentetik görüntü/ses üretir; gerçek bir sağlayıcı yayını gibi sunulmaz. Parser/depolama/provider testleri ağ sağlayıcısına bağımlı değildir. Renderer ve GUI smoke testleri çalışan bir Wayland ya da X11 oturumu gerektirir. Kesin sonuç ve sınırlar `docs/verification.md` dosyasındadır. Native Wayland kanıtı: `env -u DISPLAY -u GDK_BACKEND QT_QPA_PLATFORM=wayland ./scripts/test.sh`.

## İnceleme ve sınırlar

Smarters yalnızca kamuya açık bilgi mimarisi/kullanım akışları açısından incelendi: canlı yayın, film/dizi kütüphanesi, listeler, favoriler ve rehber. Kod, görsel varlık, marka veya ekran tasarımı kopyalanmadı. Araştırma kaynakları `docs/research.md` içindedir.

Bu sürüm kayıt, çoklu ekran, catch-up/time-shift, DRM veya ebeveyn kilidi içermez. Gerçek sağlayıcı hesabı verilmediği için Xtream doğrulaması yerel HTTP fixture ile yapılır. Sağlayıcının codec/format/erişim sınırları yayına göre değişebilir; istemci içerik ya da abonelik sağlamaz.

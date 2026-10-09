from __future__ import annotations

from .. import window as _window
from ..i18n import _, _n


class SourcesMixin:
    """Import sources and manage connections and account status."""

    def _wake_refresh(self):
        scheduler = getattr(self, "refresh_scheduler", None)
        if scheduler is not None:
            scheduler.wake()

    def _auto_refresh_available(self, source):
        if self._closed or self._busy or self._importing or self._tasks:
            return False
        return not _window.MainWindow._auto_refresh_playing(self, source)

    def _auto_refresh_playing(self, source):
        return bool(
            self.current
            and self.current.id.startswith(source["id"] + ":")
            and (
                self._playback_active
                or self._loading
                or self.recovery.state
                in {"connecting", "untracked-connecting", "waiting", "buffering"}
            )
        )

    def run_task(self, function, success, message, retry=None, busy=True, failure=None):
        if busy and self._busy:
            return
        if busy:
            self._busy = True
            self.add_button.setEnabled(False)
        if message:
            self.status(message)
        task = _window.Task(function)
        self._tasks.add(task)

        def finish():
            self._tasks.discard(task)
            # Release Qt connection closures on their owning GUI thread, even
            # when the window closed while its network request was in flight.
            task.signals.deleteLater()
            if busy:
                self._busy = False
                if not self._closed:
                    self.add_button.setEnabled(True)
            self._wake_refresh()

        def done(result):
            finish()
            if self._closed:
                return
            try:
                success(result)
            except Exception:
                message = _("Veri kaydedilemedi. Disk alanını ve dosya izinlerini kontrol edin.")
                if failure is not None:
                    failure(message)
                else:
                    self.status(message, retry)

        def failed(error):
            finish()
            if self._closed:
                return
            if failure is None:
                self.status(error, retry)
            else:
                failure(error)

        task.signals.done.connect(done)
        task.signals.failed.connect(failed)
        _window.QThreadPool.globalInstance().start(task)

    def add_source(self, checked=False, location=""):
        if self._busy or not self.guard(_("Kaynak eklemek için PIN gir.")):
            return
        dialog = _window.SourceDialog(self, location)
        if dialog.exec() == _window.QDialog.Accepted:
            self.import_source(dialog.source())

    def import_source(self, source, *, quiet=False, on_finished=None):
        from ..backup import source_incomplete

        if source_incomplete(source):
            if not quiet:
                self.status(_("Önce eksik kaynak bilgilerini «Bağlantıyı düzenle» ile tamamlayın."))
            return False
        if self._busy or self._importing or (quiet and not self._auto_refresh_available(source)):
            return False
        source = dict(source)
        self._importing = True
        completed = False
        if not quiet:
            self.channel_list.set_loading(True)
            self.filter_changed()

        def finish(success, changed=False):
            nonlocal completed
            if completed:
                return
            completed = True
            self._importing = False
            if not quiet:
                self.channel_list.set_loading(False)
                self.filter_changed()
            if on_finished is not None:
                on_finished(success, changed)
            self._wake_refresh()

        def done(result):
            if quiet:
                stored = next((s for s in self.store.sources() if s["id"] == source["id"]), None)
                if (
                    stored is None
                    or not self._same_source(stored, source)
                    or self._auto_refresh_playing(source)
                ):
                    finish(False)
                    return
            try:
                changed = (
                    self.accept_import(source, result, quiet=True)
                    if quiet
                    else self.accept_import(source, result)
                )
                if quiet and changed is None:
                    finish(False)
                elif quiet:
                    updated = next(s for s in self.store.sources() if s["id"] == source["id"])
                    if updated.get("epg_url"):
                        self.load_guide(
                            updated,
                            quiet=True,
                            on_finished=lambda success, guide_changed: finish(
                                success, changed or guide_changed
                            ),
                        )
                    else:
                        finish(True, changed)
                else:
                    finish(True)
            except Exception:
                failed(_("Veri kaydedilemedi. Disk alanını ve dosya izinlerini kontrol edin."))

        def failed(error):
            if quiet:
                self.toast.show_message(error)
            else:
                self.status(error, lambda: self.import_source(source))
            finish(False)

        def load():
            if source["type"] == "xtream":
                return _window.XtreamClient(
                    source["location"], source["username"], source["password"]
                ).catalog()
            if source["type"] == "direct":
                location = source["location"]
                scheme = _window.urlsplit(location).scheme
                if scheme not in ("http", "https", "rtsp", "rtp", "udp", "file", ""):
                    raise _window.NetworkError(_("Bu yayın protokolü desteklenmiyor."))
                if not scheme:
                    path = _window.Path(location).expanduser().resolve()
                    if not path.is_file():
                        raise _window.NetworkError(_("Video dosyası bulunamadı."))
                    location = path.as_uri()
                kind = (
                    "movie"
                    if scheme in ("", "file")
                    or _window.urlsplit(location)
                    .path.lower()
                    .endswith((".mp4", ".mkv", ".webm", ".mov", ".avi"))
                    else "live"
                )
                return _window.Playlist(
                    [
                        _window.Channel(
                            _window.channel_id(location),
                            source["name"],
                            location,
                            group="Tek yayın",
                            kind=kind,
                        )
                    ],
                    [],
                    [],
                )
            return _window.load_m3u(source["location"])

        self.run_task(
            load,
            done,
            None if quiet else _("Kaynak okunuyor…"),
            None if quiet else lambda: self.import_source(source),
            failure=failed,
        )
        return True

    def accept_import(self, source, playlist, *, quiet=False):
        if not playlist.channels:
            notify = self.toast.show_message if quiet else self.status
            notify(_("Bu kaynakta oynatılabilir yayın bulunamadı. Önceki liste korundu."))
            return
        source = dict(source)
        previous_source = next(
            (s for s in self.store.sources() if s["id"] == source.get("id")), None
        )
        previous_channels = self.store.channels(source["id"]) if source.get("id") else []
        if not source.get("epg_url") and playlist.epg_urls:
            source["epg_url"] = playlist.epg_urls[0]
        source_id = self.store.save_source(source)
        source["id"] = source_id
        if self.current and self.current.id.startswith(source_id + ":"):
            # Keep the current native request observable, but never reopen it
            # with connection data that this refresh has just superseded.
            if not self._playback_active or self._playback_token is None:
                self.recovery.cancel()
            else:
                self.recovery.suppress_retries(self._playback_token)
        if playlist.account_profile is not None:
            self.store.save_account_profile(source_id, playlist.account_profile)
        channels = list(playlist.channels)
        # A catalog has series parents only: retain cached episodes of surviving series.
        if source["type"] == "xtream":
            series_ids = {c.series_id for c in channels if c.kind == "series"}
            channels.extend(
                c
                for c in self.store.channels(source_id)
                if c.kind != "series" and c.series_id in series_ids
            )
        if self.current and self.current.id.startswith(source_id + ":"):
            self.save_progress()
        stored_channels = self.store.replace_channels(source_id, channels)
        incoming = {channel.id for channel in stored_channels}
        if self.current and self.current.id.startswith(source_id + ":"):
            if self.current.id not in incoming:
                self._playback_active = False
                self._sync_playback_state()
                self.player.stop()
                self.current = None
                self.tray.refresh()
                self.watch_panel.sync()
                self._loading = False
                self.favorite_button.setEnabled(False)
                self.video_stack.setCurrentIndex(0)
                self.video_title.setText(_("İyi bir yayına yer aç."))
        changed = previous_source != source or sorted(
            previous_channels, key=lambda c: c.id
        ) != sorted(stored_channels, key=lambda c: c.id)
        if not quiet or changed:
            self.refresh_library(select_source=None if quiet else source_id)
        if not quiet:
            # A manual import already fetched the catalogue. Do not immediately
            # download it a second time when the scheduler next becomes idle.
            last = self.store.setting("auto_refresh_last", {})
            last = dict(last) if isinstance(last, dict) else {}
            last[source_id] = _window.datetime.now().timestamp()
            self.store.set_setting("auto_refresh_last", last)
            kind = playlist.channels[0].kind
            self.set_section(kind if kind in ("live", "movie", "series") else "live")
            detail = _n(
                "{count} yayın hazır.", "{count} yayınlar hazır.", len(playlist.channels)
            ).format(count=len(playlist.channels))
            if playlist.warnings:
                detail += _n(
                    " {count} geçersiz satır atlandı.",
                    " {count} geçersiz satırlar atlandı.",
                    len(playlist.warnings),
                ).format(count=len(playlist.warnings))
            self.status(detail)
            if source.get("epg_url"):
                self.load_guide(source)
        return changed

    def source_for(self, channel):
        prefix = channel.id.split(":", 1)[0]
        return next((s for s in self.store.sources() if s["id"] == prefix), None)

    @staticmethod
    def _same_source(left, right):
        fields = ("id", "name", "type", "location", "username", "password", "epg_url")
        return all(str(left.get(field, "")) == str(right.get(field, "")) for field in fields)

    def edit_source(self, source):
        if source is None or self._busy or not self.guard(_("Bağlantıyı düzenlemek için PIN gir.")):
            return
        expected = dict(source)
        dialog = _window.SourceDialog(self, source=expected)
        if dialog.exec() != _window.QDialog.Accepted:
            return
        candidate = dialog.source()
        token = object()
        self._source_edit_tokens[expected["id"]] = token

        def is_current():
            if self._closed or self._source_edit_tokens.get(expected["id"]) is not token:
                return False
            stored = next(
                (item for item in self.store.sources() if item["id"] == expected["id"]), None
            )
            return stored is not None and self._same_source(stored, expected)

        def completed(playlist):
            if not is_current():
                return
            self._source_edit_tokens.pop(expected["id"], None)
            if not playlist.channels:
                self.status(_("Bu kaynakta oynatılabilir yayın bulunamadı. Önceki liste korundu."))
                return
            if not self.store.apply_source_connection(expected, candidate, playlist):
                self.status(_("Kaynak bu sırada değişti. Düzenleme uygulanmadı."))
                return
            if self.current and self.current.id.startswith(expected["id"] + ":"):
                if self._playback_active and self._playback_token is not None:
                    self.recovery.suppress_retries(self._playback_token)
                else:
                    self.recovery.cancel()
            self.refresh_library(select_source=expected["id"])
            self.status(_("Kaynak bağlantısı doğrulandı ve güncellendi."))
            stored = next(
                (item for item in self.store.sources() if item["id"] == expected["id"]), None
            )
            if stored is not None and stored.get("epg_url"):
                self.load_guide(stored)

        def failed(message):
            if not is_current():
                return
            self._source_edit_tokens.pop(expected["id"], None)
            self.status(message)

        self.run_task(
            lambda: _window.validate_candidate(candidate),
            completed,
            _("Yeni bağlantı doğrulanıyor…"),
            busy=True,
            failure=failed,
        )

    def check_source(self, source):
        if source is None:
            return
        expected = dict(source)
        token = object()
        self._source_health_tokens[expected["id"]] = token

        def is_current():
            if self._closed or self._source_health_tokens.get(expected["id"]) is not token:
                return False
            stored = next(
                (item for item in self.store.sources() if item["id"] == expected["id"]), None
            )
            return stored is not None and self._same_source(stored, expected)

        def completed(result):
            if not is_current():
                return
            self._source_health_tokens.pop(expected["id"], None)
            if not self.store.save_source_health(expected["id"], result.status, result.checked_at):
                return
            message = {
                "available": _("Bağlantı kullanılabilir."),
                "responding": _("Sunucu yanıt veriyor; video akışı açılmadı."),
                "unverified": _("Adres geçerli; video akışı açılmadan doğrulanamıyor."),
                "unavailable": _("Bağlantıya ulaşılamadı."),
            }.get(result.status, _("Bağlantı durumu belirlenemedi."))
            self.status(message)

        def failed(_message):
            completed(_window.HealthResult("unavailable", int(_window.datetime.now().timestamp())))

        self.run_task(
            lambda: _window.check_connection(expected),
            completed,
            _("Bağlantı kontrol ediliyor…"),
            busy=False,
            failure=failed,
        )

    def open_account(self, source):
        if source is None or source.get("type") != "xtream":
            return None
        if self._account_dialog is not None:
            if _window.isValid(self._account_dialog):
                self._account_dialog.close()
            self._account_dialog = None
        dialog = _window.AccountDialog(
            source["name"], self.store.account_profile(source["id"]), self
        )
        dialog.source_id = source["id"]
        self._account_dialog = dialog

        def closed(*_args):
            if self._account_dialog is dialog:
                self._account_dialog = None

        dialog.closed.connect(closed)
        dialog.destroyed.connect(closed)
        dialog.refresh_requested.connect(lambda: self.refresh_account(source, dialog))
        dialog.show()
        self.refresh_account(source, dialog)
        return dialog

    def refresh_account(self, source, dialog):
        if dialog.is_refreshing or not dialog.accepts_updates:
            return
        dialog.set_refreshing(True)

        def current_dialog():
            if self._account_dialog is not dialog or not _window.isValid(dialog):
                return False
            stored = next(
                (item for item in self.store.sources() if item["id"] == source["id"]), None
            )
            return (
                dialog.accepts_updates and stored is not None and self._same_source(stored, source)
            )

        def refreshed(profile):
            if not current_dialog():
                return
            try:
                profile = _window.sanitize_profile(profile)
                self.store.save_account_profile(source["id"], profile)
                dialog.render(profile)
            except Exception:
                if current_dialog():
                    dialog.show_error(_("Hesap profili güvenli biçimde işlenemedi."))
            finally:
                if current_dialog():
                    dialog.set_refreshing(False)

        def failed(message):
            if not current_dialog():
                return
            try:
                dialog.show_error(message)
            finally:
                if current_dialog():
                    dialog.set_refreshing(False)

        self.run_task(
            lambda: _window.XtreamClient(
                source["location"], source["username"], source["password"]
            ).account_info(),
            refreshed,
            None,
            busy=False,
            failure=failed,
        )

    def rename_source(self, source):
        if source is None or not self.guard(_("Kaynağı yeniden adlandırmak için PIN gir.")):
            return
        name, accepted = _window.QInputDialog.getText(
            self,
            _("Kaynağı yeniden adlandır"),
            _("Kaynak adı"),
            _window.QLineEdit.Normal,
            source["name"],
        )
        if not accepted:
            return
        try:
            renamed = self.store.rename_source(source["id"], name)
        except ValueError as error:
            self.status(str(error))
            return
        if not renamed:
            self.status(_("Kaynak artık mevcut değil."))
            return
        index = self.source_combo.findData(source["id"])
        if index >= 0:
            self.source_combo.setItemText(index, name.strip())
        self.status(_("Kaynak adı güncellendi."))

    def remove_source(self, source):
        if not self.guard(_("Kaynağı kaldırmak için PIN gir.")):
            return
        if (
            _window.QMessageBox.question(
                self,
                _("Kaynağı kaldır"),
                _("“{name}” ve bu kaynağın favorileri kaldırılsın mı?").format(name=source["name"]),
                _window.QMessageBox.Yes | _window.QMessageBox.No,
            )
            != _window.QMessageBox.Yes
        ):
            return
        self._source_edit_tokens.pop(source["id"], None)
        self._source_health_tokens.pop(source["id"], None)
        if self.current and self.current.id.startswith(source["id"] + ":"):
            self.close_current()
        account_dialog = self._account_dialog
        if account_dialog is not None:
            if not _window.isValid(account_dialog):
                self._account_dialog = None
            elif getattr(account_dialog, "source_id", None) == source["id"]:
                account_dialog.close()
        self.store.remove_source(source["id"])
        self._guide_data.pop(source["id"], None)
        self._guide_index.pop(source["id"], None)
        (self.store.path.parent / f"epg-{source['id']}.xml").unlink(missing_ok=True)
        self.refresh_library()
        self.status(_("Kaynak kaldırıldı."))

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and event.mimeData().urls()[0].isLocalFile():
            event.acceptProposedAction()

    def dropEvent(self, event):
        path = event.mimeData().urls()[0].toLocalFile()
        self.add_source(location=path)
        event.acceptProposedAction()

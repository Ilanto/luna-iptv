from __future__ import annotations

from .. import window as _window


class GuideMixin:
    """Load EPG data and update guide views."""

    def show_guide(self):
        """The Rehber page; the browse filters keep their state for when people return."""
        self.page_transition.begin(self.library_pages.currentWidget() is not self.guide_view)
        for key, button in self.nav_buttons.items():
            button.setChecked(key == "guide")
        self.library_pages.setCurrentWidget(self.guide_view)
        self.refresh_guide_view()
        self.guide_view.go_now()
        self.page_transition.end()

    def refresh_guide_view(self):
        rows, seen = [], set()
        for channel in self.model.channels:
            if channel.kind != "live" or not channel.tvg_id or channel.id in seen:
                continue
            if self.proxy.hide_locked and channel.id in self.model.locked:
                continue
            index = self._guide_index.get(channel.id.split(":", 1)[0])
            if index is not None and channel.tvg_id in index.channel_ids():
                rows.append((channel, index))
                seen.add(channel.id)
        self.guide_view.can_remind = True
        reminded = {(r["channel_id"], r["start"]) for r in self.reminder_service.reminders()}
        self.guide_view.set_rows(rows, reminded)

    def programme_hits(self, query, hours=24):
        """How many programmes of the next ``hours`` match ``query`` on visible channels."""
        key = _window.search_key(query)
        if len(key) < 2 or not self._guide_index:
            return 0
        start = _window.datetime.now(_window.timezone.utc)
        end = start + _window.timedelta(hours=hours)
        by_source = {}
        for channel in self.model.channels:
            if channel.kind != "live" or not channel.tvg_id:
                continue
            if self.proxy.hide_locked and channel.id in self.model.locked:
                continue
            source = channel.id.split(":", 1)[0]
            if self.proxy.source and source != self.proxy.source:
                continue
            by_source.setdefault(source, set()).add(channel.tvg_id)
        return sum(
            len(index.search(key, start, end, by_source[source]))
            for source, index in self._guide_index.items()
            if source in by_source
        )

    def configure_guide(self):
        source_id = self.source_combo.currentData()
        source = next((s for s in self.store.sources() if s["id"] == source_id), None)
        if source is None and self.current:
            source = self.source_for(self.current)
        if source is None:
            self.status("Önce soldan bir kaynak seç. Rehber o kaynağa bağlanacak.")
            return
        dialog = _window.GuideDialog(source.get("epg_url", ""), self)
        if dialog.exec() == _window.QDialog.Accepted:
            source["epg_url"] = dialog.location.text().strip()
            self.store.save_source(source)
            self.refresh_home()
            if source["epg_url"]:
                self.load_guide(source)

    def load_cached_guides(self):
        if self._closed:
            return
        for source in self.store.sources():
            path = self.store.path.parent / f"epg-{source['id']}.xml"
            if path.exists():
                self.load_guide(source, cached=True)

    def load_guide(self, source, cached=False, *, quiet=False, on_finished=None):
        path = self.store.path.parent / f"epg-{source['id']}.xml"

        def read():
            location = str(path) if cached else source.get("epg_url", "")
            if _window.urlsplit(location).scheme in ("http", "https"):
                raw = _window.fetch(location)
            else:
                from urllib.request import url2pathname

                filename = (
                    url2pathname(_window.urlsplit(location).path)
                    if location.startswith("file:")
                    else location
                )
                with _window.Path(filename).expanduser().open("rb") as stream:
                    raw = stream.read(_window.LIMIT + 1)
                if len(raw) > _window.LIMIT:
                    raise _window.NetworkError("Rehber boyut sınırını aşıyor.")
                if raw.startswith(b"\x1f\x8b"):
                    import io

                    with _window.gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:
                        raw = stream.read(_window.LIMIT + 1)
                    if len(raw) > _window.LIMIT:
                        raise _window.NetworkError("Açılmış rehber boyut sınırını aşıyor.")
            return raw, _window.parse_xmltv(raw)

        def finish(success, changed=False):
            if on_finished is not None:
                on_finished(success, changed)

        def done(result):
            stored = next((s for s in self.store.sources() if s["id"] == source["id"]), None)
            if stored is None or (quiet and not self._same_source(stored, source)):
                finish(False)
                return
            raw, programmes = result
            changed = self._guide_data.get(source["id"]) != programmes
            if not cached:
                import os

                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(raw)
            self._guide_data[source["id"]] = programmes
            self._guide_index[source["id"]] = _window.GuideIndex(programmes)
            self._refresh_live_cards()
            if self.library_pages.currentWidget() is self.guide_view:
                self.refresh_guide_view()
            self.update_guide()
            if not quiet:
                self.status(
                    f"Program rehberi hazır · {len(programmes):,} program.".replace(",", ".")
                )
            finish(True, changed)

        def failed(error):
            if quiet:
                self.toast.show_message(error)
            else:
                self.status(error, lambda: self.load_guide(source))
            finish(False)

        self.run_task(
            read,
            done,
            None if quiet else "Program rehberi okunuyor…",
            None if quiet else lambda: self.load_guide(source),
            busy=quiet,
            failure=failed,
        )

    def programme_now(self, channel):
        """What a live card shows as on now; cheap enough to call while painting."""
        if channel.kind != "live" or not channel.tvg_id:
            return None
        index = self._guide_index.get(channel.id.split(":", 1)[0])
        return index.now(channel.tvg_id) if index else None

    def _refresh_live_cards(self):
        if self._guide_index and not self._closed:
            self.channel_list.viewport().update()
            if self.library_pages.currentWidget() is self.home_view:
                self.home_view.refresh_programmes()

    def update_guide(self):
        if self._closed:
            return
        self._sync_mpris()
        if not self.current:
            return
        if self.current.kind != "live":
            self.now_title.setText("Kaldığın yer hatırlanır.")
            self.next_title.setText(
                "Ses, altyazı ve baştan başlatma seçenekleri Oynatma menüsünde."
            )
            return
        source = self.source_for(self.current)
        index = self._guide_index.get(source["id"]) if source else None
        now = index.now(self.current.tvg_id) if index else None
        upcoming = index.upcoming(self.current.tvg_id, 4) if index else []
        self.now_title.setText(
            f"ŞİMDİ  {now.start.astimezone():%H:%M} — {now.end.astimezone():%H:%M}   {now.title}"
            if now
            else "Bu kanal için güncel program bulunamadı."
        )
        self.next_title.setText(
            "SIRADA\n"
            + "\n".join(f"{item.start.astimezone():%H:%M}   {item.title}" for item in upcoming)
            if upcoming
            else "Rehber kanal kimliği, listedeki tvg-id ile eşleşmelidir."
        )

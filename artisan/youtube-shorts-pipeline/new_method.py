def process_video_for_shorts(self, video_id: str, niche: Optional[str] = None,
                                  force: bool = False,
                                  local_only: bool = False,
                                  source_channel: str = '') -> bool:
        """Audio-only discovery -> transcribe -> find highlights -> section fetch -> render -> (upload).

        Every stage is resumable: an existing audio download, transcript, section
        files, or rendered clips are reused instead of being redone. Returns True
        if at least one Short is on disk when we finish.

        This avoids ever downloading the full source video (1-2 GB). Instead:
          1. Audio-only fetch (~40 MB for an hour) for discovery transcription
          2. Section fetch (clip ranges only, ~few MB each) for rendering

        Args:
            local_only: never download; fail if audio/sections not already cached.
            source_channel: the configured source handle the video came from
                (e.g. ``@AlexHormozi``). Stored on the processed-video row so
                the performance feedback loop can rank sources.
        """
        video_id = extract_video_id(video_id) or video_id
        logger.info("Starting processing for video %s", video_id)

        if not force and self.db.is_video_processed(video_id):
            logger.warning(
                "Video %s was already processed (use --force to redo it)", video_id
            )
            return False

        audio_path = None
        section_files = []
        try:
            # -- 1. audio-only download (or reuse) --------------------------
            logger.info("Step 1/6: Fetching audio for discovery (reusing existing if present)")
            if local_only:
                existing_audio = self.downloader.find_local_audio(video_id)
                if not existing_audio:
                    logger.error(
                        "No local audio for %s in %s. Run without --from-library to download.",
                        video_id, self.downloader.audio_dir,
                    )
                    self.stats['errors'] += 1
                    return False
                metadata = self.downloader._audio_metadata(video_id, existing_audio)
            else:
                metadata = self.downloader.download_audio(video_id)

            if not metadata or not metadata.get('audio_path'):
                logger.error("Could not obtain audio for %s", video_id)
                self.stats['errors'] += 1
                return False

            audio_path = metadata['audio_path']
            if not Path(audio_path).exists():
                logger.error("Audio file vanished: %s", audio_path)
                self.stats['errors'] += 1
                return False

            title = metadata.get('title') or video_id
            duration = metadata.get('duration') or 0
            logger.info(
                "%s: '%s' (%ss, %.1f MB)",
                "Reused existing audio" if metadata.get('from_cache') else "Downloaded audio",
                title, duration, Path(audio_path).stat().st_size / (1024 * 1024),
            )

            if niche is None:
                niche = guess_niche(metadata)
            niche_config = self.config.get_niche_config(niche)
            niche_keywords = niche_config.get('keywords', [])
            logger.info(
                "Niche '%s': %d keywords %s",
                niche, len(niche_keywords), niche_keywords[:5],
            )

            # -- 2. transcribe (or reuse the cache) -------------------------
            logger.info("Step 2/6: Transcribing audio (cached transcripts are reused)")
            transcript = None if force else self.load_cached_transcript(video_id)

            if transcript is None:
                max_seconds = getattr(self.transcriber, 'max_seconds', None)
                transcript = self.transcriber.transcribe_audio(audio_path, max_seconds=max_seconds)
                if not transcript:
                    logger.error("Transcription produced nothing for %s", audio_path)
                    self.stats['errors'] += 1
                    return False
                logger.info("Transcribed audio into %d segments", len(transcript))
                self.save_transcript(video_id, transcript, title)

            # -- 3. find highlights ----------------------------------------
            logger.info("Step 3/6: Finding highlight segments")
            clip_cap = self.config.max_clips_per_video
            if source_channel:
                perf = (self.db.source_performance() or {}).get(source_channel) or {}
                if perf.get('recorded') and float(perf.get('avg_views') or 0) >= \
                        self.config.winner_avg_views:
                    clip_cap = max(clip_cap, self.config.max_clips_per_video_winner)
                    logger.info(
                        "Source '%s' is a proven winner (avg %.0f views) -- "
                        "raising clip cap to %d",
                        source_channel, float(perf.get('avg_views') or 0), clip_cap,
                    )
            highlights = self.processor.find_highlight_segments(
                transcript,
                niche_keywords=niche_keywords,
                min_segment_length=self.config.min_segment_length,
                max_segment_length=self.config.max_segment_length,
                min_gap_between=self.config.min_gap_between_clips,
                max_clips=clip_cap,
                max_candidates=getattr(self.config, 'max_candidates', None),
                min_score=float(niche_config.get('min_score') or 0.0),
            )
            if not highlights:
                logger.warning("No highlight segments found for video %s", video_id)
                self.stats['errors'] += 1
                return False
            logger.info("Found %d highlight segments", len(highlights))

            # Phase 6: cache the full ranked candidate list for --render-more
            if not force and getattr(self.config, 'max_candidates', None):
                plan = {
                    'video_id': video_id,
                    'title': title,
                    'niche': niche,
                    'niche_keywords': niche_keywords,
                    'transcript_span': float(transcript[-1]['end']) - float(transcript[0]['start']),
                    'candidates': highlights,
                }
                self.save_clip_plan(video_id, plan)

            # -- 4. fetch sections (only the clip ranges) ------------------
            logger.info("Step 4/6: Fetching clip sections (%.1f MB each vs full video)",
                        self.config.section_padding * 2)
            ranges = [(h['start'], h['end']) for h in highlights]
            sections = self.downloader.download_sections(
                video_id, ranges,
                padding=self.config.section_padding,
                concurrency=self.config.download_concurrency,
                force_redownload=force,
            )

            # Filter out failed section downloads
            valid_highlights = []
            valid_sections = []
            for h, s in zip(highlights, sections):
                if s and s.get('path'):
                    valid_highlights.append(h)
                    valid_sections.append(s)
                else:
                    logger.warning("Section download failed for clip %.1f-%.1fs, skipping",
                                   h['start'], h['end'])

            if not valid_highlights:
                logger.error("No sections could be downloaded for %s", video_id)
                self.stats['errors'] += 1
                return False

            highlights = valid_highlights
            section_files = valid_sections
            logger.info("Fetched %d/%d sections successfully", len(highlights), len(ranges))

            # -- 5. render from section files -------------------------------
            safe_title = sanitize_filename(title) or video_id
            shorts_dir = Path(self.config.shorts_dir) / niche / safe_title
            shorts_dir.mkdir(parents=True, exist_ok=True)

            logger.info("Step 5/6: Creating Shorts from section files")
            self.db.record_video(
                video_id, title, niche, duration,
                channel_id=source_channel or (metadata.get('uploader', '') or ''),
                published_at=metadata.get('upload_date'),
            )

            created = []
            for i, (highlight, section) in enumerate(zip(highlights, section_files), start=1):
                hook_text = (highlight.get('text') or '').strip()
                safe_hook = sanitize_filename(hook_text) if hook_text else f"clip{i}"
                if len(safe_hook) > 50:
                    safe_hook = safe_hook[:50]
                output_path = str(shorts_dir / f"{i:02d}_{safe_hook}.mp4")
                existing = Path(output_path)

                if not force and existing.exists() and existing.stat().st_size > 64 * 1024:
                    logger.info(
                        "Resume: clip %d/%d already rendered (%.1f MB) -- skipping",
                        i, len(highlights), existing.stat().st_size / (1024 * 1024),
                    )
                    self.stats['shorts_created'] += 1
                    created.append({'index': i, 'path': output_path, 'highlight': highlight})
                    self.db.record_short(
                        video_id, i, highlight['start'], highlight['end'],
                        title=title, local_path=output_path,
                        score=highlight.get('score'),
                    )
                    continue

                logger.info(
                    "Rendering clip %d/%d: %.1f-%.1fs (score %.2f)",
                    i, len(highlights), highlight['start'], highlight['end'],
                    highlight.get('score', 0.0),
                )

                section_path = section['path']
                clip_start_in_file = section['clip_start_in_file']
                clip_duration = section['clip_duration']

                # Build clip-relative transcript from the section's audio
                clip_transcript = [
                    seg for seg in transcript
                    if not (seg['end'] <= highlight['start']
                            or seg['start'] >= highlight['end'])
                ]

                # Use the section file as source; timestamps are rebased by clip_start_in_file
                ok = self.video_editor.create_short_from_segment(
                    video_path=section_path,
                    start_time=clip_start_in_file,
                    end_time=clip_start_in_file + clip_duration,
                    transcript_segments=clip_transcript,
                    output_path=output_path,
                    add_branding=False,
                    captions_are_clip_relative=False,
                )

                if not ok or not Path(output_path).exists():
                    logger.error("Failed to create clip %d", i)
                    self.stats['errors'] += 1
                    continue

                self.stats['shorts_created'] += 1
                created.append({'index': i, 'path': output_path, 'highlight': highlight})
                self.db.record_short(
                    video_id, i, highlight['start'], highlight['end'],
                    title=title, local_path=output_path,
                    score=highlight.get('score'),
                )

            if not created:
                logger.error("No clips could be rendered for %s", video_id)
                return False

            # -- 6. upload --------------------------------------------------
            if self.upload_enabled:
                logger.info("Step 6/6: Uploading %d Shorts", len(created))
                self._upload_clips(created, video_id, niche, niche_keywords)
            else:
                logger.info(
                    "Step 6/6: Upload disabled (set UPLOAD_ENABLED=true to publish). "
                    "%d clips kept locally.", len(created)
                )

            self.stats['videos_processed'] += 1
            logger.info(
                "Finished %s -- %d clips in %s",
                video_id, len(created), shorts_dir,
            )
            return True

        except KeyboardInterrupt:
            logger.warning("Interrupted while processing %s", video_id)
            raise
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", video_id, exc, exc_info=True)
            self.stats['errors'] += 1
            return False
        finally:
            # Clean up audio (regenerable). Section files are small and kept
            # for resume; they're in data/temp/sections/ and auto-managed.
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
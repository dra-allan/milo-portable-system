def process_video_for_shorts(self, video_id: str, niche: str) -> bool:
        """
        Process a single YouTube video to create Shorts

        Args:
            video_id: YouTube video ID to process
            niche: Niche category (for keyword boosting)

        Returns:
            True if at least one Short was successfully uploaded, False otherwise
        """
        logger.info(f"Starting processing for video {video_id} in niche '{niche}'")

        try:
            # Step 1: Download video (or reuse existing)
            logger.info("Step 1: Downloading video")
            video_metadata = self.downloader.download_video(video_id)
            if not video_metadata:
                logger.error(f"Failed to download video {video_id}")
                self.stats['errors'] += 1
                return False

            video_path = video_metadata['video_path']
            title = video_metadata['title']
            duration = video_metadata['duration']

            logger.info(f"Downloaded: '{title}' ({duration}s)")

            # Step 2: Extract audio and transcribe
            logger.info("Step 2: Extracting audio and transcribing")
            audio_path = self.transcriber.extract_audio_from_video(video_path)
            if not audio_path:
                logger.error(f"Failed to extract audio from {video_path}")
                self.stats['errors'] += 1
                return False

            transcript = self.transcriber.transcribe_audio(audio_path)
            if not transcript:
                logger.error(f"Failed to transcribe audio from {audio_path}")
                self.stats['errors'] += 1
                # Clean up audio file
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
                # Keep video and subtitle files for reuse
                return False

            logger.info(f"Transcribed audio into {len(transcript)} segments")

            # Step 3: Get niche-specific keywords
            niche_config = self.config.get_niche_config(niche)
            niche_keywords = niche_config.get('keywords', [])
            logger.info(f"Using {len(niche_keywords)} keywords for niche '{niche}': {niche_keywords[:5]}...")

            # Step 4: Find highlight segments
            logger.info("Step 3: Finding highlight segments")
            highlights = self.processor.find_highlight_segments(
                transcript,
                niche_keywords=niche_keywords,
                min_segment_length=self.config.min_segment_length,
                max_segment_length=self.config.max_segment_length
            )

            if not highlights:
                logger.warning(f"No highlight segments found for video {video_id}")
                # Clean up audio file only
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
                # Keep video and subtitle files
                return False

            logger.info(f"Found {len(highlights)} highlight segments")

            # Step 5: Prepare output directory for shorts (named after video title)
            safe_title_folder = sanitize_filename(title)
            shorts_dir = Path(__file__).parent.parent / 'data' / 'shorts' / safe_title_folder
            shorts_dir.mkdir(parents=True, exist_ok=True)

            # Step 6: Process each highlight into a Short
            logger.info("Step 4: Creating Shorts from highlights")
            shorts_created = 0

            for i, highlight in enumerate(highlights):
                logger.info(f"Processing highlight {i+1}/{len(highlights)}: {highlight['start']:.1f}-{highlight['end']:.1f}s")

                # Get transcript segments for this highlight
                highlight_transcript = [
                    seg for seg in transcript
                    if not (seg['end'] <= highlight['start'] or seg['start'] >= highlight['end'])
                ]

                # Create output filename inside the titled folder
                output_filename = f"{i+1:02d}_{safe_title_folder}.mp4"
                output_path = str(shorts_dir / output_filename)

                # Create the Short
                success = self.video_editor.create_short_from_segment(
                    video_path=video_path,
                    start_time=highlight['start'],
                    end_time=highlight['end'],
                    transcript_segments=highlight_transcript,
                    output_path=output_path,
                    add_branding=True  # Add intro/outro branding
                )

                if success and Path(output_path).exists():
                    # Step 7: Upload the Short
                    logger.info(f"Step 5: Uploading Short {i+1}")

                    # Generate title and description
                    short_title = f"[{niche.upper()}] {highlight['text'][:50]}... #Shorts"
                    short_description = f"""Full video: https://youtube.com/watch?v={video_id}

Follow for more {niche} content!
#Shorts #{niche} {' '.join([f'#{kw}' for kw in niche_keywords[:3]])}
"""

                    # Prepare tags
                    tags = [niche, "Shorts"] + [kw for kw in niche_keywords if kw]

                    # Upload to YouTube
                    youtube_video_id = self.uploader.upload_short(
                        video_path=output_path,
                        title=short_title,
                        description=short_description,
                        tags=tags
                    )

                    if youtube_video_id:
                        logger.info(f"Successfully uploaded Short: {youtube_video_id}")
                        self.stats['shorts_uploaded'] += 1
                        shorts_created += 1

                        # TODO: Record in database that this short was created from this video
                    else:
                        logger.error(f"Failed to upload Short {i+1}")
                        self.stats['errors'] += 1

                    # Keep the short file for user access (do NOT delete after upload)
                else:
                    logger.error(f"Failed to create Short {i+1}")
                    self.stats['errors'] += 1

            # Step 8: Final cleanup
            logger.info("Step 6: Cleaning up")
            self.stats['videos_processed'] += 1
            self.stats['shorts_created'] += shorts_created

            # Clean up audio file only (to save space); keep video and subtitle files for reuse
            try:
                os.remove(audio_path)
            except OSError:
                pass
            # Video file and subtitle file are left in temp directory (renamed to title) for future use

            logger.info(f"Finished processing video {video_id}: {shorts_created} Shorts created and kept in {shorts_dir}")
            return shorts_created > 0

        except Exception as e:
            logger.error(f"Unexpected error processing video {video_id}: {str(e)}", exc_info=True)
            self.stats['errors'] += 1
            return False
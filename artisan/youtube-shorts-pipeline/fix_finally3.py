with open('src/main.py', 'r') as f:
    content = f.read()

# Replace the try/except/finally with try/except (cleanup in except and before return)
old = """            return True
        except KeyboardInterrupt:
            logger.warning("Interrupted while processing %s", video_id)
            raise
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", video_id, exc, exc_info=True)
            self.stats['errors'] += 1
            return False
        finally:
            # Clean up audio regardless of success or exception
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass

    def _upload_"""

new = """            # Clean up audio on success
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            return True
        except KeyboardInterrupt:
            logger.warning("Interrupted while processing %s", video_id)
            # Clean up audio on interrupt
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            raise
        except Exception as exc:
            logger.error("Unexpected error processing %s: %s", video_id, exc, exc_info=True)
            self.stats['errors'] += 1
            # Clean up audio on error
            if audio_path:
                try:
                    os.remove(audio_path)
                except OSError:
                    pass
            return False

    def _upload_"""

if old in content:
    content = content.replace(old, new)
    with open('src/main.py', 'w') as f:
        f.write(content)
    print('Restructured - removed finally block')
else:
    print('Pattern not found')"
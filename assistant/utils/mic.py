import asyncio
import speech_recognition as sr
from assistant.utils.logging import get_logger

LOGGER = get_logger("system_mic")

async def listen_to_system_mic(timeout: int = 5, phrase_time_limit: int = 15) -> str:
    """Listens to the system microphone and returns transcribed text."""
    def _record_and_transcribe():
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                LOGGER.info("Calibrating system microphone for ambient noise...")
                recognizer.adjust_for_ambient_noise(source, duration=1)
                LOGGER.info("Listening now...")
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
                LOGGER.info("Audio captured, transcribing...")
                return recognizer.recognize_google(audio)
        except sr.WaitTimeoutError:
            LOGGER.warning("Listening timed out while waiting for phrase to start")
            return ""
        except sr.UnknownValueError:
            LOGGER.warning("Speech recognition could not understand audio")
            return ""
        except sr.RequestError as e:
            LOGGER.error(f"Could not request results from Speech Recognition service; {e}")
            return ""
        except Exception as e:
            LOGGER.exception(f"Unexpected error occurred during recording: {e}")
            return ""

    return await asyncio.to_thread(_record_and_transcribe)

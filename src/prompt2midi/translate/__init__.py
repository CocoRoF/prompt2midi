"""translate: sentence -> Intent v1 (extraction only). Vocabulary: translate/lexicon/*.json. See docs/rule-provider.md."""
from .extract import Extraction, load_lexicon, translate
from .keys import find_key, find_keys, find_mode_word, find_time_signature

__all__ = ["Extraction", "find_key", "find_keys", "find_mode_word", "find_time_signature", "load_lexicon", "translate"]

"""Closed sets shared by persistence, API, and model-output contracts."""

from enum import Enum, IntEnum


class AgeUnit(str, Enum):
    """Units owners commonly use to state a cat's age."""

    MONTHS = "months"
    YEARS = "years"


class WeightUnit(str, Enum):
    """Supported cat weight units."""

    KILOGRAMS = "kg"
    POUNDS = "lb"


class EnergyLevel(IntEnum):
    """Owner-reported energy level on a bounded five-point scale."""

    ONE = 1
    TWO = 2
    THREE = 3
    FOUR = 4
    FIVE = 5


class CatSex(str, Enum):
    """Owner-reported sex, with an explicit unknown value for genuine uncertainty."""

    MALE = "male"
    FEMALE = "female"
    UNKNOWN = "unknown"


class FeedbackReason(str, Enum):
    """Why a response was unhelpful.

    These are not cosmetic categories: each points at a different fix, which is
    the entire reason for asking. `WRONG_INFORMATION` and `DID_NOT_ANSWER`
    indicate retrieval or corpus problems — the wrong entries were surfaced, or
    none covered the question. `NOT_SPECIFIC_TO_MY_CAT` and `TOO_CAUTIOUS`
    indicate the retrieval was fine and the prompt is at fault. Reading the
    reason alongside the generation trace tells you which without guessing.
    """

    WRONG_INFORMATION = "wrong_information"
    NOT_SPECIFIC_TO_MY_CAT = "not_specific_to_my_cat"
    DID_NOT_ANSWER = "did_not_answer"
    TOO_CAUTIOUS = "too_cautious"
    OTHER = "other"


class AuthStatus(str, Enum):
    """Whether an auth attempt produced a usable session."""

    ACTIVE = "active"
    CONFIRMATION_REQUIRED = "confirmation_required"


class Corner(str, Enum):
    """The four deliberately separate product surfaces."""

    BEHAVIOR = "behavior"
    HEALTH = "health"
    FUN_FACTS = "fun-facts"
    SPECIAL_MOMENTS = "special-moments"


class BodySystem(str, Enum):
    """Body-system values present in MASTER_health_corpus.csv."""

    DENTAL = "dental"
    DIGESTIVE = "digestive"
    EARS = "ears"
    EYES = "eyes"
    KIDNEY = "kidney"
    MUSCULOSKELETAL = "musculoskeletal"
    NEUROLOGICAL = "neurological"
    RESPIRATORY = "respiratory"
    SKIN = "skin"
    SYSTEMIC = "systemic"
    TOXIN = "toxin"
    URINARY = "urinary"


class UrgencyTier(str, Enum):
    """Health urgency values present in MASTER_health_corpus.csv."""

    EMERGENCY = "emergency"
    URGENT = "urgent"
    MONITOR = "monitor"
    ROUTINE = "routine"


class TriageResponseKind(str, Enum):
    """Closed health-response states used by safety logic and analytics."""

    TRIAGE = "triage"
    EMERGENCY_CANNED = "emergency_canned"
    NO_RELIABLE_INFORMATION = "no_reliable_information"


class BehaviorCategory(str, Enum):
    """Behavior categories present in MASTER_behavior_corpus.csv."""

    ENVIRONMENT = "environment"
    SOCIAL = "social"
    COMMUNICATION = "communication"
    STRESS_SIGNALS = "stress-signals"
    NORMAL_BEHAVIOR = "normal-behavior"


class ConfidenceLevel(str, Enum):
    """Allowed confidence labels for behavior evidence and interpretations."""

    WELL_ESTABLISHED = "well-established"
    GENERAL = "general"
    VARIES_BY_CAT = "varies-by-cat"


class BehaviorAnswerMode(str, Enum):
    """Whether a behavior answer is sourced or explicitly general knowledge."""

    CORPUS_GROUNDED = "corpus_grounded"
    GENERAL_KNOWLEDGE = "general_knowledge"


class FunFactCategory(str, Enum):
    """Categories present in MASTER_fun_facts.csv."""

    AGE = "age"
    BEHAVIOR = "behavior"
    BREED = "breed"
    COAT = "coat"
    COGNITION = "cognition"
    COMMUNICATION = "communication"
    HISTORY = "history"
    SENSES = "senses"


class FunFactTone(str, Enum):
    """Presentation tones present in MASTER_fun_facts.csv."""

    PLAYFUL = "playful"
    INFORMATIVE = "informative"


class MomentKind(str, Enum):
    """Supported scrapbook item types."""

    PHOTO = "photo"
    VIDEO = "video"
    NOTE = "note"
    DATE = "date"


class MessageRole(str, Enum):
    """Roles persisted in a user-visible chat session."""

    USER = "user"
    ASSISTANT = "assistant"


class AppetiteChange(str, Enum):
    """Explicit appetite states available to structured symptom extraction."""

    UNKNOWN = "unknown"
    NO_CHANGE = "no-change"
    DECREASED = "decreased"
    INCREASED = "increased"
    NOT_EATING = "not-eating"


class VomitingFrequency(str, Enum):
    """Explicit vomiting states available to structured symptom extraction."""

    UNKNOWN = "unknown"
    NONE = "none"
    ONCE = "once"
    REPEATED = "repeated"


class FeedbackThumb(str, Enum):
    """Binary feedback choices."""

    UP = "up"
    DOWN = "down"


class ToolErrorCode(str, Enum):
    """Failure categories that tool implementations return to orchestration."""

    INVALID_INPUT = "invalid-input"
    NOT_FOUND = "not-found"
    UNAVAILABLE = "unavailable"
    UNAUTHORIZED = "unauthorized"
    CONFLICT = "conflict"
    INTERNAL = "internal"

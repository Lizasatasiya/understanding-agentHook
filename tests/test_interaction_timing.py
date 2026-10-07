import time
from unittest.mock import patch, MagicMock
from understanding_agent.interaction import Interaction


def test_interaction_initial_response_time():
    interaction = Interaction()
    assert interaction.last_response_time == 0


def test_interaction_non_interactive_fallback():
    interaction = Interaction()
    with patch("sys.stdin.isatty", return_value=False), \
         patch("builtins.open", side_effect=OSError("No TTY")):
        ans = interaction.timed_input("Prompt: ", 60)
        assert ans == "Non-interactive mock answer"
        assert interaction.last_response_time == 1


def test_speech_does_not_inflate_response_time():
    interaction = Interaction()

    # Simulate speak_question taking some time
    def mock_speak(text):
        time.sleep(0.01)

    with patch.object(interaction, "_speak_question", side_effect=mock_speak), \
         patch.object(interaction, "_timed_input_unix") as mock_input:
        
        def fake_timed_input_unix(prompt, timeout, seed_text="", allow_mic=False, hint_shown=False):
            interaction.last_response_time = 12
            return "my answer"

        mock_input.side_effect = fake_timed_input_unix

        ans = interaction.timed_input("Prompt: ", 60, speak_text="A very long question")
        assert ans == "my answer"
        assert interaction.last_response_time == 12

from setuptools import setup, find_packages

setup(
    name="understanding-agent",
    version="0.2.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "understanding-agent = understanding_agent.cli:main",
        ]
    },
    install_requires=[],
    extras_require={
        # Optional: enables voice answer input via microphone + Whisper STT
        # Install with: pip install understanding-agent[voice]
        "voice": [
            "openai-whisper",
            "pyaudio",
        ]
    },
)

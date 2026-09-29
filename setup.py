from setuptools import setup, find_packages

setup(
    name="understanding-agent",
    version="0.2.2",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "understanding-agent = understanding_agent.cli:main",
        ]
    },
    install_requires=[],
    extras_require={
        "voice": [
            "sounddevice",
            "openai-whisper",
            "numpy",
        ]
    },
)

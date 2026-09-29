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
    install_requires=[
        "openai-whisper",
        "pyaudio",
        "numpy",
    ],
    extras_require={
        "voice": [
            "openai-whisper",
            "pyaudio",
            "numpy",
        ]
    },
)

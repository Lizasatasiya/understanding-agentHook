from setuptools import setup, find_packages

setup(
    name="understanding-agent",
    version="0.3.0",
    packages=find_packages(),
    package_data={"understanding_agent": ["ui/*"]},
    include_package_data=True,
    entry_points={
        "console_scripts": [
            "understanding-agent = understanding_agent.cli:main",
            "understanding-agent-ui = understanding_agent.server:run",
        ]
    },
    python_requires=">=3.10",
    install_requires=[],
    extras_require={
        "voice": [
            "sounddevice",
            "openai-whisper",
            "numpy",
        ],
        "ui": [
            "fastapi",
            "uvicorn",
            "pydantic>=2",
        ],
    },
)

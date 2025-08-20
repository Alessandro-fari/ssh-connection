from setuptools import setup, find_packages

setup(
    name="ssh-connection",
    version="1.0.0",
    description="SSH Connection Manager with System Tray Integration",
    author="DevAgent",
    package_dir={"": "src"},
    packages=find_packages(where="src"),
    install_requires=[
        "pystray>=0.19.0",
        "pillow>=9.0.0", 
        "pyyaml>=6.0",
        "cryptography>=3.4.0",
        "pyautogui>=0.9.50",
        "psutil>=5.8.0",
    ],
    entry_points={
        "console_scripts": [
            "ssh-connection=ssh_connection.main:main",
        ],
    },
    python_requires=">=3.8",
)
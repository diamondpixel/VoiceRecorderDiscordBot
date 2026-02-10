# VoiceRecord Discord Bot

A Discord bot that records voice audio from users in a voice channel to `.ogg` files.

## Features
-   Records specific users to local `.ogg` files.
-   Handles Opus packets directly from Discord.
-   Patched to fix common `voice_recv` crashes.

## Setup

1.  **Install Dependencies:**
    ```bash
    pip install -U discord.py[voice] discord-ext-voice-recv av numpy python-dotenv
    ```

    *Note: You may need to install `libopus` and `ffmpeg` separately depending on your OS.*

2.  **Configuration:**
    -   Create a `.env` file in the root directory.
    -   Add your bot token:
        ```env
        DISCORD_TOKEN=your_token_here
        ```

3.  **Run:**
    ```bash
    python main.py
    ```

## Commands
-   `/join`: Join your current voice channel.
-   `/startrecord <user> <path>`: Start recording a user.
-   `/stoprecord`: Stop recording and save the file.
-   `/leave`: Leave the voice channel.

## License & Disclaimer
This project is open-source and free to use for any purpose.

**DISCLAIMER:** This software is provided for **educational purposes only**. The authors and contributors are not responsible for any misuse, harm, or legal consequences arising from the use of this bot. It is your responsibility to ensure you comply with all applicable laws and Discord's Terms of Service regarding audio recording and consent.

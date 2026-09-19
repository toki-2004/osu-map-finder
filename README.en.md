[中文](README.md) | English

# osu-map-finder

A small Windows tool to search osu! beatmaps, download them in one click and unpack them into a folder you choose. Single-file Python (tkinter), no third-party dependencies.

## Features

- Reads the song currently playing on your system (Windows SMTC) and prefills the search box; you can also type any keyword
- Advanced filters: mode, status, search scope, genre, language, plus Stars / AR / OD / CS / HP / BPM / Length ranges
- Each result row shows title, artist, mapper, mode, Stars, BPM and length, with a one-click download button on the right
- The downloaded osz is renamed to zip, extracted into `artist - title\` under your save path, and the zip is deleted afterwards

## Usage

Download the zip from Releases and run `osu-map-finder.exe`; or run from source:

    python osu_map_finder.py

The save path is configurable at the bottom of the window and is stored in `config.json` next to the program. The default is a `beatmaps/` folder beside it.

## Data source and credits

- Beatmap search and download mirror are provided by **Sayobot** (osu.sayobot.cn). Thanks to Sayobot for keeping a public, login-free search and mirror service available
- The underlying beatmap metadata comes from the official osu! database, mirrored by Sayobot
- This tool is only a search/download client. It hosts and mirrors no beatmap files
- This tool is not affiliated with osu! or Sayobot

## Copyright

- Songs, beatmaps and all assets inside them belong to their respective creators and rights holders
- This tool does not modify or redistribute any of that content. Please respect the osu! terms and the rights of the creators, and do not use the downloaded content commercially

## License

MIT License, see LICENSE.

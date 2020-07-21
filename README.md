# trackr

An IRC bot that checks player lists for all servers in a

## Usage

 - Make sure you are using the latest miniirc and miniirc_extras.
    - `sudo pip3 install --upgrade miniirc miniirc_extras`
 - Copy and edit `trackr.example.ini`.
 - Run `./trackr.py /path/to/trackr.ini`

## Moderation commands

 - `,mute <player>`: Mutes a player permanently.
 - `,unmute <player>`: Unmutes a player.
 - `,tempmute <player> <duration>`: Temporarily mutes a player for a specified
    duration. The duration cannot be greater than 2 hours. If the server is
    shut down before this duration is up, the tempmute is ended prematurely due
    to technical limitations with trackr.
 - `,warn <player> <message>`: Shows the player a warning dialog.
 - `,kick <player> <reason>`: Kicks a player.
 - `,badservers`: Lists the servers trackr isn't logged into.

### Parameter format

 - `player`: A Minetest/MultiCraft/??? player. This can be the player name if
    the player is in one (and only one) server on the channel. If the player
    is in multiple servers, you can do `player_name@server_name`.
 - `duration` (default: 5 minutes): The duration to mute the player for. By
    default, this is in minutes, however this can be changed by appending `s`
    (seconds), `m` (minutes), or `h` (hours).
 - `message`: The message to tell the player.
 - `reason`: The reason for kicking the player. This is shown to the player
    when being kicked.

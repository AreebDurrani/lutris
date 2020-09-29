import json
from datetime import datetime
from gettext import gettext as _

from gi.repository import Gio, Gtk

from lutris import runners, services
from lutris.config import LutrisConfig
from lutris.database.games import add_or_update, get_games
from lutris.game import Game
from lutris.gui.widgets.utils import get_link_button, get_pixbuf_for_game
from lutris.util.strings import gtk_safe


class GameBar(Gtk.Fixed):
    play_button_position = (12, 42)

    def __init__(self, db_game, game_actions):
        """Create the game bar with a database row"""
        super().__init__(visible=True)
        self.set_margin_bottom(12)
        self.game_actions = game_actions
        self.db_game = db_game
        if db_game.get("service"):
            self.service = services.get_services()[db_game["service"]]()
        else:
            self.service = None
        game_id = None
        if "service_id" in db_game:  # Any field that isn't in service game. Not ideal
            game_id = db_game["id"]
        elif self.service:
            existing_games = get_games(filters={"service_id": db_game["appid"], "service": self.service.id})
            if existing_games:
                game_id = existing_games[0]["id"]
        if game_id:
            self.game = Game(game_id)
        else:
            self.game = Game()
        self.game_name = db_game["name"]
        self.game_slug = db_game["slug"]
        self.put(self.get_game_name_label(), 16, 8)
        if self.game:
            game_actions.set_game(self.game)
        x_offset = 140
        y_offset = 42
        if self.game.is_installed:
            self.put(self.get_runner_button(), x_offset, y_offset)
            self.put(self.get_runner_label(), x_offset + 45, y_offset)
            x_offset += 135
        if self.game.lastplayed:
            self.put(self.get_last_played_label(), x_offset, y_offset)
            x_offset += 95
        if self.game.playtime:
            self.put(self.get_playtime_label(), x_offset, y_offset)

        self.put_play_button()

    def get_popover(self, buttons):
        """Return the popover widget containing a list of link buttons"""
        popover = Gtk.Popover()
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, visible=True)

        for action in buttons:
            vbox.pack_end(buttons[action], False, False, 1)
        popover.add(vbox)
        popover.set_position(Gtk.PositionType.TOP)
        return popover

    def get_icon(self):
        """Return the game icon"""
        icon = Gtk.Image.new_from_pixbuf(get_pixbuf_for_game(self.game_slug, (32, 32)))
        icon.show()
        return icon

    def get_game_name_label(self):
        """Return the label with the game's title"""
        title_label = Gtk.Label(visible=True)
        title_label.set_markup("<span font_desc='16'><b>%s</b></span>" % gtk_safe(self.game_name))
        return title_label

    def get_runner_button(self):
        icon_name = self.game.runner.name + "-symbolic"
        runner_icon = Gtk.Image.new_from_icon_name(icon_name, Gtk.IconSize.MENU)
        runner_icon.show()
        runner_button = Gtk.MenuButton()
        runner_button.set_image(runner_icon)
        runner_button.set_popover(self.get_popover(self.get_runner_buttons()))
        runner_button.show()
        return runner_button

    def get_runner_label(self):
        runner_label = Gtk.Label(visible=True)
        if len(self.game.platform) > 15:
            platform = self.game.platform[:15] + "…"
        else:
            platform = self.game.platform
        runner_label.set_markup("Platform:\n<b>%s</b>" % gtk_safe(platform))
        return runner_label

    def get_playtime_label(self):
        """Return the label containing the playtime info"""
        playtime_label = Gtk.Label(visible=True)
        playtime_label.set_markup(_("Time played:\n<b>%s</b>") % self.game.formatted_playtime)
        return playtime_label

    def get_last_played_label(self):
        """Return the label containing the last played info"""
        last_played_label = Gtk.Label(visible=True)
        lastplayed = datetime.fromtimestamp(self.game.lastplayed)
        last_played_label.set_markup(_("Last played:\n<b>%s</b>") % lastplayed.strftime("%x"))
        return last_played_label

    def put_play_button(self):
        if self.service:
            button = Gtk.Button(visible=True)
            button.set_size_request(120, 36)
            if self.service.online:
                button.set_label(_("Install"))
                button.connect("clicked", self.on_install_clicked)
            else:
                button.set_label(_("Play"))
                button.connect("clicked", self.on_play_clicked)
            widget = button
        else:
            box = Gtk.HBox(visible=True)
            style_context = box.get_style_context()
            style_context.add_class("linked")
            button = Gtk.Button(visible=True)
            button.set_size_request(84, 32)
            popover_button = Gtk.MenuButton(visible=True)
            popover_button.set_size_request(32, 32)
            popover_button.props.direction = Gtk.ArrowType.UP
            popover_button.set_popover(self.get_popover(self.get_game_buttons()))
            if self.game.is_installed:
                button.set_label(_("Play"))
                button.connect("clicked", self.game_actions.on_game_run)
            else:
                button.set_label(_("Install"))
                button.connect("clicked", self.game_actions.on_install_clicked)
            box.add(button)
            box.add(popover_button)
            widget = box
        self.put(widget, self.play_button_position[0], self.play_button_position[1])

    def get_game_buttons(self):
        """Return a dictionary of buttons to use in the panel"""
        displayed = self.game_actions.get_displayed_entries()
        buttons = {}
        for action in self.game_actions.get_game_actions():
            action_id, label, callback = action
            if action_id in ("play", "stop", "install"):
                continue
            button = get_link_button(label)
            if displayed.get(action_id):
                button.show()
            else:
                button.hide()
            buttons[action_id] = button
            button.connect("clicked", callback)
        return buttons

    def get_runner_buttons(self):
        buttons = {}
        if self.game.runner_name and self.game.is_installed:
            runner = runners.import_runner(self.game.runner_name)(self.game.config)
            for entry in runner.context_menu_entries:
                name, label, callback = entry
                button = get_link_button(label)
                button.show()
                button.connect("clicked", callback)
                buttons[name] = button
        return buttons

    def on_install_clicked(self, button):
        """Handler for installing service games"""
        print("Installing:")
        print(self.db_game)
        print("Service:")
        print(self.service)

    def on_play_clicked(self, button):
        """Handler for launching service games"""
        config_id = self.game_slug + "-" + self.service.id
        if self.service.id == "xdg":
            runner = "linux"
            game_id = add_or_update(
                name=self.game_name,
                runner=runner,
                slug=self.game_slug,
                installed=1,
                configpath=config_id,
                installer_slug="desktopapp",
                service=self.service.id,
                service_id=self.db_game["appid"],
            )
            self.create_xdg_config(config_id)
            game = Game(game_id)
            application = Gio.Application.get_default()
            application.launch(game)
        elif self.service.id == "steam":
            runner = "steam"
            game_id = add_or_update(
                name=self.game_name,
                runner=runner,
                slug=self.game_slug,
                installed=1,
                configpath=config_id,
                installer_slug="steam",
                service=self.service.id,
                service_id=self.db_game["appid"],
            )
            self.create_steam_config(config_id)
            game = Game(game_id)
            application = Gio.Application.get_default()
            application.launch(game)

    def create_steam_config(self, config_id):
        """Create the game configuration for a Steam game"""
        game_config = LutrisConfig(runner_slug="steam", game_config_id=config_id)
        game_config.raw_game_config.update({"appid": self.db_game["appid"]})
        game_config.save()

    def create_xdg_config(self, config_id):
        details = json.loads(self.db_game["details"])
        config = LutrisConfig(runner_slug="linux", game_config_id=config_id)
        config.raw_game_config.update(
            {
                "exe": details["exe"],
                "args": details["args"],
            }
        )
        config.raw_system_config.update({"disable_runtime": True})
        config.save()

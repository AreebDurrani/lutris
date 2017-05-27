# application.py
#
# Copyright (C) 2016 Patrick Griffis <tingping@tingping.se>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import json
import logging
import os
import signal
import gettext
from gettext import gettext as _

import gi
gi.require_version('Gdk', '3.0')
gi.require_version('Gtk', '3.0')
from gi.repository import Gio, GLib, Gtk

from lutris import pga
from lutris.config import check_config
from lutris.platforms import update_platforms
from lutris.gui.dialogs import ErrorDialog, InstallOrPlayDialog
from lutris.migrations import migrate
from lutris.thread import exec_in_thread
from lutris.util import datapath
from lutris.util.log import logger
from lutris.util.resources import parse_installer_url
from lutris.services.steam import (AppManifest, get_appmanifests,
                                   get_steamapps_paths)

from .lutriswindow import LutrisWindow


class Application(Gtk.Application):
    def __init__(self):
        super().__init__(application_id='net.lutris.Lutris',
                         flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

        gettext.bindtextdomain("lutris", "/usr/share/locale")
        gettext.textdomain("lutris")

        check_config()
        migrate()
        update_platforms()

        GLib.set_application_name(_('Lutris'))
        self.window = None
        self.css_provider = Gtk.CssProvider.new()

        try:
            self.css_provider.load_from_path(os.path.join(datapath.get(), 'ui', 'lutris.css'))
        except GLib.Error as e:
            logger.exception(e)

        if hasattr(self, 'add_main_option'):
            self.add_arguments()
        else:
            ErrorDialog("Your Linux distribution is too old, Lutris won't function properly")

    def add_arguments(self):
        self.add_main_option('debug',
                             ord('d'),
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Show debug messages'),
                             None)
        self.add_main_option('install',
                             ord('i'),
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING,
                             _('Install a game from a yml file'),
                             None)
        self.add_main_option('exec',
                             ord('e'),
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING,
                             _('Execute a program with the lutris runtime'),
                             None)
        self.add_main_option('list-games',
                             ord('l'),
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('List all games in database'),
                             None)
        self.add_main_option('installed',
                             ord('o'),
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Only list installed games'),
                             None)
        self.add_main_option('list-steam-games',
                             ord('s'),
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('List available Steam games'),
                             None)
        self.add_main_option('list-steam-folders',
                             0,
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('List all known Steam library folders'),
                             None)
        self.add_main_option('json',
                             ord('j'),
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Display the list of games in JSON format'),
                             None)
        self.add_main_option('reinstall',
                             0,
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.NONE,
                             _('Reinstall game'),
                             None)
        self.add_main_option(GLib.OPTION_REMAINING,
                             0,
                             GLib.OptionFlags.NONE,
                             GLib.OptionArg.STRING_ARRAY,
                             _('uri to open'),
                             'URI')

    def do_startup(self):
        Gtk.Application.do_startup(self)
        signal.signal(signal.SIGINT, signal.SIG_DFL)

        action = Gio.SimpleAction.new('quit')
        action.connect('activate', lambda *x: self.quit())
        self.add_action(action)
        self.add_accelerator('<Primary>q', 'app.quit')

        builder = Gtk.Builder.new_from_file(
            os.path.join(datapath.get(), 'ui', 'menus.ui')
        )
        appmenu = builder.get_object('app-menu')
        self.set_app_menu(appmenu)

    def do_activate(self):
        if not self.window:
            self.window = LutrisWindow(application=self)
            screen = self.window.props.screen
            Gtk.StyleContext.add_provider_for_screen(
                screen,
                self.css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self.window.present()

    @staticmethod
    def _print(command_line, string):
        # Workaround broken pygobject bindings
        command_line.do_print_literal(command_line, string + '\n')

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()

        # Set up logger
        if options.contains('debug'):
            logger.setLevel(logging.DEBUG)

        # Text only commands

        # List game
        if options.contains('list-games'):
            game_list = pga.get_games()
            if options.contains('installed'):
                game_list = [game for game in game_list if game['installed']]
            if options.contains('json'):
                self.print_game_json(command_line, game_list)
            else:
                self.print_game_list(command_line, game_list)
            return 0
        # List Steam games
        elif options.contains('list-steam-games'):
            self.print_steam_list(command_line)
            return 0
        # List Steam folders
        elif options.contains('list-steam-folders'):
            self.print_steam_folders(command_line)
            return 0

        # Execute command in Lutris context
        elif options.contains('exec'):
            command = options.lookup_value('exec').get_string()
            self.execute_command(command)
            return 0

        try:
            url = options.lookup_value(GLib.OPTION_REMAINING)
            installer_info = self.get_lutris_action(url)
        except ValueError:
            self._print(command_line, '%s is not a valid URI' % url.get_strv())
            return 1
        game_slug = installer_info['game_slug']
        action = installer_info['action']
        revision = installer_info['revision']

        installer_file = None
        if options.contains('install'):
            installer_file = options.lookup_value('install').get_string()
            installer_file = os.path.abspath(installer_file)
            action = 'install'
            if not os.path.isfile(installer_file):
                self._print(command_line, "No such file: %s" % installer_file)
                return 1

        # Graphical commands
        self.activate()

        db_game = None
        if game_slug:
            if action == 'rungameid':
                # Force db_game to use game id
                db_game = pga.get_game_by_field(game_slug, 'id')
            elif action == 'rungame':
                # Force db_game to use game slug
                db_game = pga.get_game_by_field(game_slug, 'slug')
            elif action == 'install':
                # Installers can use game or installer slugs
                db_game = (pga.get_game_by_field(game_slug, 'slug') or
                           pga.get_game_by_field(game_slug, 'installer_slug'))

            else:
                # Dazed and confused, try anything that might works
                db_game = (pga.get_game_by_field(game_slug, 'id') or
                           pga.get_game_by_field(game_slug, 'slug') or
                           pga.get_game_by_field(game_slug, 'installer_slug'))

        if not action:
            if db_game and db_game['installed']:
                # Game found but no action provided, ask what to do
                dlg = InstallOrPlayDialog(db_game['name'])
                if not dlg.action_confirmed:
                    action = None
                if dlg.action == 'play':
                    action = 'rungame'
                elif dlg.action == 'install':
                    action = 'install'
            elif game_slug or installer_file:
                # No game found, default to install if a game_slug or
                # installer_file is provided
                action = 'install'

        if action == 'install':
            self.window.on_install_clicked(game_slug=game_slug,
                                           installer_file=installer_file,
                                           revision=revision)
        elif action in ('rungame', 'rungameid'):
            logger.info("Launching %s" % db_game['name'])
            self.window.on_game_run(game_id=db_game['id'])

        return 0

    def get_lutris_action(self, url):
        installer_info = {
            'game_slug': None,
            'revision': None,
            'action': None
        }

        if url:
            url = url.get_strv()

        if url and len(url):
            url = url[0]  # TODO: Support multiple
            installer_info = parse_installer_url(url)
            if installer_info is False:
                raise ValueError
        return installer_info

    def print_game_list(self, command_line, game_list):
        for game in game_list:
            self._print(
                command_line,
                "{:4} | {:<40} | {:<40} | {:<15} | {:<64}".format(
                    game['id'],
                    game['name'][:40],
                    game['slug'][:40],
                    game['runner'] or '-',
                    game['directory'] or '-'
                )
            )

    def print_game_json(self, command_line, game_list):
        games = [
            {
                'id': game['id'],
                'slug': game['slug'],
                'name': game['name'],
                'runner': game['runner'],
                'directory': game['directory']
            }
            for game in game_list
        ]
        self._print(command_line, json.dumps(games, indent=2))

    def print_steam_list(self, command_line):
        steamapps_paths = get_steamapps_paths()
        for platform in ('linux', 'windows'):
            for path in steamapps_paths[platform]:
                appmanifest_files = get_appmanifests(path)
                for appmanifest_file in appmanifest_files:
                    appmanifest = AppManifest(os.path.join(path, appmanifest_file))
                    self._print(
                        command_line,
                        "  {:8} | {:<60} | {:10} | {}".format(
                            appmanifest.steamid,
                            appmanifest.name or '-',
                            platform,
                            ", ".join(appmanifest.states)
                        )
                    )

    def execute_command(self, command):
        """
            Execute an arbitrary command in a Lutris context
            with the runtime enabled and monitored by LutrisThread
        """
        logger.info("Running command '{}'".format(command))
        thread = exec_in_thread(command)
        try:
            GLib.MainLoop().run()
        except KeyboardInterrupt:
            thread.stop()

    def print_steam_folders(self, command_line):
        steamapps_paths = get_steamapps_paths()
        for platform in ('linux', 'windows'):
            for path in steamapps_paths[platform]:
                self._print(command_line, path)

    def do_shutdown(self):
        logger.info("Shutting down Lutris")
        Gtk.Application.do_shutdown(self)
        if self.window:
            self.window.destroy()

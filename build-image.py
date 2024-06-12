#!/usr/bin/env python

import os
import sys
import yaml

from subprocess import run as exec_host

class colors:
    reset = '\033[0m'
    bold = '\033[01m'
    disable = '\033[02m'
    underline = '\033[04m'
    reverse = '\033[07m'
    strikethrough = '\033[09m'
    invisible = '\033[08m'

    class fg:
        black = '\033[30m'
        red = '\033[31m'
        green = '\033[32m'
        orange = '\033[33m'
        blue = '\033[34m'
        purple = '\033[35m'
        cyan = '\033[36m'
        lightgrey = '\033[37m'
        darkgrey = '\033[90m'
        lightred = '\033[91m'
        lightgreen = '\033[92m'
        yellow = '\033[93m'
        lightblue = '\033[94m'
        pink = '\033[95m'
        lightcyan = '\033[96m'

        rainbow = [lightred, orange, yellow,
                   lightgreen, lightcyan, blue, purple]
        seq = 0

        def random(self):
            if self.seq == 7:
                self.seq = 0
            self.seq += 1
            return self.rainbow[self.seq - 1]

        def clear_seq(self):
            self.seq = 0

    class bg:
        black = '\033[40m'
        red = '\033[41m'
        green = '\033[42m'
        orange = '\033[43m'
        blue = '\033[44m'
        purple = '\033[45m'
        cyan = '\033[46m'
        lightgrey = '\033[47m'


fg = colors.fg()


def info(msg):
    print(colors.bold + fg.cyan + '[INFO] ' +
          colors.reset + msg + colors.reset)


def warn(warning):
    print(colors.bold + fg.yellow + '[WARNING] ' +
          colors.reset + warning + colors.reset)


def error(err):
    print(colors.bold + fg.red + '[ERROR] ' +
          colors.reset + err + colors.reset)


def proceed():
    print(colors.bold + fg.red + '[QUESTION] ' +
          colors.reset + 'would you like to proceed?' + colors.reset)
    info(f'(press {colors.bold}ENTER{colors.reset} to proceed, or {colors.bold}^C{colors.reset}/{colors.bold}^D{colors.reset} to cancel)')
    input()


##


if not os.path.isfile('recipe.yaml'):
    error('No recipe.yaml file!')
    sys.exit(1)

if not os.path.isfile('ingredients.yaml'):
    error('No ingredients.yaml file!')
    sys.exit(1)

if os.geteuid() != 0:
    error('Not running as root!')
    sys.exit(1)

with open('recipe.yaml') as recipe_file:
    image_recipe = yaml.safe_load(recipe_file)

# Create the build directory
exec_host(['rm', '-rf', '.build'])
exec_host(['mkdir', '.build'])

if image_recipe['base-image'] == 'none' or image_recipe.get('replace-repos') == True:
    # Generate a pacman file
    with open('.build/pacman.conf', 'w') as pacman_config_file:
        # Include default pacman options
        pacman_config_file.write('''# Your system will not reflect any changes in this file.
[options]
Architecture = auto
ParallelDownloads = 16
SigLevel = Required DatabaseOptional
LocalFileSigLevel = Never
''')

        # System repositories
        for repo in image_recipe['repos']:
            pacman_config_file.write(f'''
[{repo["name"]}]
Server = {repo["url"]}
''')

if image_recipe['base-image'] == 'none':
    # Pacstrap a new rootfs
    exec_host(['mkdir', '.build/rootfs'])
    tries = 0
    while (exec_host(['pacstrap', '-McKC', 'pacman.conf', 'rootfs', 'base', 'sudo'], cwd='.build').returncode != 0) and (tries < 15):
        tries += 1
        exec_host(['rm', '-rf', '.build/rootfs'])
        exec_host(['mkdir', '.build/rootfs'])

    # Copy new pacman.conf
    exec_host(['cp', '.build/pacman.conf', '.build/rootfs/etc/pacman.conf'])
else:
    # Pull image squashfs
    tries = 0
    while (exec_host(['wget', '-O', '.build/base-image.squashfs', image_recipe['base-image']]).returncode != 0) and (tries < 5):
        exec_host(['rm', '-f', '.build/base-image.squashfs'])

    # Unsquashfs
    exec_host(['unsquashfs', 'base-image.squashfs'], cwd='.build')
    exec_host(['mv', '.build/squashfs-root', '.build/rootfs'])

    if image_recipe.get('replace-repos') == True:
        # Copy new pacman.conf
        exec_host(['cp', '.build/pacman.conf', '.build/rootfs/etc/pacman.conf'])
    elif type(image_recipe.get('repos')) == list:
        # Add repositories
        with open('.build/rootfs/etc/pacman.conf', 'a') as pacman_config_file:
            for repo in image_recipe['repos']:
                pacman_config_file.write(f'''
[{repo["name"]}]
Server = {repo["url"]}
''')

        # Copy pacman.conf to .build
        exec_host('cp', '.build/rootfs/etc/pacman.conf', '.build/pacman.conf')

# Build PKGBUILDs and copy them to a local repository
if image_recipe['pkgbuilds-dir'] != None:
    exec_host(['cp', '-a', '.build/rootfs', '.build/pkg-builder-rootfs'])
    exec_host(['cp', '-a', image_recipe['pkgbuilds-dir'], '.build/pkg-builder-rootfs/pkgbuilds'])

    # Add a user for package builds
    exec_host(['systemd-nspawn', '-D', '.build/pkg-builder-rootfs', 'useradd', '-m', '-G', 'wheel', '-s', '/bin/bash', 'aur'])
    exec_host(['systemd-nspawn', '-D', '.build/pkg-builder-rootfs', 'bash', '-c', 'echo "aur ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/aur'])

    # Change ownership of pkgbuilds dir
    exec_host(['systemd-nspawn', '-D', '.build/pkg-builder-rootfs', 'chown', '-R', 'aur', '/pkgbuilds'])

    # Install base-devel & git
    exec_host(['systemd-nspawn', '-D', '.build/pkg-builder-rootfs', 'pacman', '-Sy', '--noconfirm', 'base-devel', 'git'])

    # Create the package repo dir
    exec_host(['mkdir', '.build/rootfs/packages'])

    # Build each package
    built_package_list = []
    for package in os.listdir(image_recipe['pkgbuilds-dir']):
        exec_host(['systemd-nspawn', '-D', '.build/pkg-builder-rootfs', 'runuser', '-u', 'aur', '--', 'env', '-C', f'/pkgbuilds/{package}',
                   'makepkg', '-si', '--noconfirm'])
        for built_package in os.listdir(f'.build/pkg-builder-rootfs/pkgbuilds/{package}'):
            if '.pkg.tar.' in built_package:
                exec_host(['cp', f'.build/pkg-builder-rootfs/pkgbuilds/{package}/' + built_package, '.build/rootfs/packages'])
                built_package_list.append(built_package)

    # Generate new package repo db
    exec_host(['repo-add', 'packages.db.tar.gz', *built_package_list], cwd=f'.build/rootfs/packages')

    # Add package repo to temporary /etc/pacman.conf
    with open('.build/rootfs/etc/pacman.conf', 'a') as pacman_config_file:
        pacman_config_file.write(f'''
[packages]
SigLevel = Never
Server = file:///packages
''')

# Install packages from ingredients file
with open('ingredients.yaml', 'r') as ingredients_file:
    image_ingredients = yaml.safe_load(ingredients_file)
exec_host(['systemd-nspawn', '-D', '.build/rootfs', 'pacman', '-Sy', '--noconfirm', *image_ingredients['packages']])

# Copy back original /etc/pacman.conf
exec_host(['cp', '.build/pacman.conf', '.build/rootfs/etc/pacman.conf'])

# Enable services
if type(image_ingredients.get('services')) == list:
    for service in image_ingredients['services']:
        exec_host(['systemd-nspawn', '-D', '.build/rootfs', 'systemctl', 'enable', service])

# Enable user services
if type(image_ingredients.get('user-services')) == list:
    for service in image_ingredients['user-services']:
        exec_host(['systemd-nspawn', '-D', '.build/rootfs', 'systemctl', 'enable', '--global', service])

# Build image
exec_host(['rm', '-f', f'{image_recipe["id"]}.squashfs'])
exec_host(['mksquashfs', '.build/rootfs', f'{image_recipe["id"]}.squashfs'])

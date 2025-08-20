# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['run.py'],
    pathex=['C:\\WORKSPACE_PROGETTI_INIZIALI\\ssh-connection\\src'],
    binaries=[],
    datas=[('C:\\WORKSPACE_PROGETTI_INIZIALI\\ssh-connection\\resources', 'resources')],
    hiddenimports=['ssh_connection', 'ssh_connection.main', 'ssh_connection.gui.tray_icon_manager', 'ssh_connection.ssh.ssh_config_parser', 'ssh_connection.ssh.ssh_launcher', 'ssh_connection.config.config_loader', 'ssh_connection.security.crypto_util'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SSH-Connection-Manager',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['resources\\icon.ico'],
)

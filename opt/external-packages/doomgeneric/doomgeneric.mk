################################################################################
#
# doomgeneric
#
# DOOM for the SeedSigner panel. The platform layer lives in this repository
# under boot-game/doom; only the engine is fetched.
#
################################################################################

DOOMGENERIC_VERSION = dcb7a8dbc7a16ce3dda29382ac9aae9d77d21284
DOOMGENERIC_SITE = $(call github,ozkl,doomgeneric,$(DOOMGENERIC_VERSION))
DOOMGENERIC_LICENSE = GPL-2.0
DOOMGENERIC_LICENSE_FILES = LICENSE
# The panel config generator below runs on the host's Python.
DOOMGENERIC_DEPENDENCIES = host-python3

# Where our platform layer and the panel config generator live.
# BR2_EXTERNAL_RPI_SEEDSIGNER_PATH is <repo>/opt/pi0, and docker-compose
# mounts both ./opt and ./boot-game, so these resolve inside the container.
DOOMGENERIC_BOOTGAME = $(BR2_EXTERNAL_RPI_SEEDSIGNER_PATH)/../../boot-game/doom
DOOMGENERIC_APP_SRC = $(BR2_EXTERNAL_RPI_SEEDSIGNER_PATH)/../rootfs-overlay/opt

# The engine's own source list, minus its platform entry points.
DOOMGENERIC_UNITS = dummy am_map doomdef doomstat dstrings d_event d_items \
	d_iwad d_loop d_main d_mode d_net f_finale f_wipe g_game hu_lib hu_stuff \
	info i_cdmus i_endoom i_joystick i_scale i_sound i_system i_timer memio \
	m_argv m_bbox m_cheat m_config m_controls m_fixed m_menu m_misc m_random \
	p_ceilng p_doors p_enemy p_floor p_inter p_lights p_map p_maputl p_mobj \
	p_plats p_pspr p_saveg p_setup p_sight p_spec p_switch p_telept p_tick \
	p_user r_bsp r_data r_draw r_main r_plane r_segs r_sky r_things sha1 \
	sounds statdump st_lib st_stuff s_sound tables v_video wi_stuff \
	w_checksum w_file w_main w_wad z_zone w_file_stdc i_input i_video \
	doomgeneric

DOOMGENERIC_CFLAGS = -std=gnu99 -O2 -DNORMALUNIX -DLINUX -D_DEFAULT_SOURCE \
	-DDOOMGENERIC_RESX=320 -DDOOMGENERIC_RESY=200 \
	-Wno-implicit-function-declaration

# The panel configuration is generated from the SeedSigner checkout that
# build.sh has already placed in the rootfs overlay, so pins, geometry and the
# init sequence come from the code that drives this hardware rather than from a
# transcription that could drift.
define DOOMGENERIC_GENERATE_PANEL_CONFIG
	$(HOST_DIR)/bin/python3 $(DOOMGENERIC_BOOTGAME)/tools/gen_panel_config.py \
		--app $(DOOMGENERIC_APP_SRC) \
		--profile $(SEEDSIGNER_HARDWARE_PROFILE) \
		--display $(SEEDSIGNER_DISPLAY_CONFIG) \
		--out $(@D)/ss_panel_config.h
endef
DOOMGENERIC_PRE_BUILD_HOOKS += DOOMGENERIC_GENERATE_PANEL_CONFIG

SEEDSIGNER_HARDWARE_PROFILE ?= RPI_40
SEEDSIGNER_DISPLAY_CONFIG ?= st7789_320x240

define DOOMGENERIC_BUILD_CMDS
	cp $(DOOMGENERIC_BOOTGAME)/src/ss_*.c $(DOOMGENERIC_BOOTGAME)/src/ss_*.h \
		$(DOOMGENERIC_BOOTGAME)/src/dg_seedsigner.c $(@D)/doomgeneric/
	cp $(@D)/ss_panel_config.h $(@D)/doomgeneric/
	cd $(@D)/doomgeneric && $(TARGET_CC) $(DOOMGENERIC_CFLAGS) -I. \
		-o doom-seedsigner \
		$(addsuffix .c,$(DOOMGENERIC_UNITS)) \
		ss_video.c ss_unlock.c ss_gpio.c ss_display.c ss_input.c dg_seedsigner.c \
		-lm
endef

define DOOMGENERIC_INSTALL_TARGET_CMDS
	$(INSTALL) -D -m 0755 $(@D)/doomgeneric/doom-seedsigner \
		$(TARGET_DIR)/usr/local/games/doom
	$(INSTALL) -D -m 0644 $(DOOMGENERIC_BOOTGAME)/wad/freedoom1.wad \
		$(TARGET_DIR)/usr/local/games/freedoom1.wad
endef

$(eval $(generic-package))

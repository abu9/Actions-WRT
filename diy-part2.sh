#!/bin/bash
#
# Copyright (c) 2019-2020 P3TERX <https://p3terx.com>
#
# This is free software, licensed under the MIT License.
# See /LICENSE for more information.
#
# https://github.com/P3TERX/Actions-OpenWrt
# File name: diy-part2.sh
# Description: OpenWrt DIY script part 2 (After Update feeds)
#


# ./scripts/feeds update
# ./scripts/feeds uninstall luci-app-fileassistant zerotier cloudflared golang
# ./scripts/feeds install -p immortal_pkg -f golang zerotier cloudflared luci-app-filebrowser
# ./scripts/feeds install -p jell -f luci-app-fileassistant luci-app-adguardhome luci-app-log

# Modify default IP
sed -i 's/192.168.1.1/192.168.2.254/g' package/base-files/files/bin/config_generate

#fix vlmcsd compile issue with ccache and external-toolchain
patch -p1 -d feeds/packages <../patchs/vlmcsd.patch

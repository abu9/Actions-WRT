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
echo 'src-git jell https://github.com/kenzok8/jell' >>feeds.conf.default
./scripts/feeds update jell
./scripts/feeds uninstall luci-app-fileassistant
./scripts/feeds install -p jell -f -d y luci-app-fileassistant luci-app-adguardhome luci-app-shadowsocks-rust
./scripts/feeds uninstall libffi
# Modify default IP
#sed -i 's/192.168.1.1/192.168.50.5/g' package/base-files/files/bin/config_generate
# curl -fsSL git.io/file-transfer | sh
sed -i 's/192.168.1.1/192.168.2.254/g' package/base-files/files/bin/config_generate
# sed -i 's/radio\${devidx}\.disabled=1/radio\${devidx}\.disabled=0/g' package/kernel/mac80211/files/lib/wifi/mac80211.sh
patch -p0 -i files/enable-vlan-for-mtwifi.patch
patch -p0 -i files/remove-luci-uhttpd.patch
#git apply files/Kmemleak.patch
#rm target/linux/generic/pending-5.4/761-net-dsa-mt7530-Support-EEE-features.patch

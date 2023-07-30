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
function merge_package(){
    repo=$(echo "$1" | rev | cut -d'/' -f 1 | rev)
    pkg=$(echo "$2" | rev | cut -d'/' -f 1 | rev)
    find package/ -follow -name "$pkg" -not -path "package/custom/*" -print0 | xargs -0 -rt rm -rf
    git clone --depth=1 --single-branch "$1"
    mv "$2" package/custom/
    rm -rf "$repo"
}
function drop_package(){
    find package/ -follow -name "$1" -not -path "package/custom/*" -print0 | xargs -0 -rt rm -rf
}

echo 'src-git jell https://github.com/kenzok8/jell' >>feeds.conf.default
echo 'src-git immortal_pkg https://github.com/immortalwrt/packages' >>feeds.conf.default
./scripts/feeds update
./scripts/feeds uninstall luci-app-fileassistant zerotier cloudflared golang
./scripts/feeds install -p immortal_pkg -f golang
./scripts/feeds install -p jell -f -d y luci-app-fileassistant luci-app-adguardhome luci-app-log
./scripts/feeds install -p immortal_pkg -f -d y zerotier cloudflared
#./scripts/feeds uninstall libffi
# Modify default IP
#sed -i 's/192.168.1.1/192.168.50.5/g' package/base-files/files/bin/config_generate
# curl -fsSL git.io/file-transfer | sh
sed -i 's/192.168.1.1/192.168.2.254/g' package/base-files/files/bin/config_generate
# sed -i 's/radio\${devidx}\.disabled=1/radio\${devidx}\.disabled=0/g' package/kernel/mac80211/files/lib/wifi/mac80211.sh
#git apply files/enable-vlan-for-mtwifi.patch
# patch -p0 -i files/fix-libffi-build.patch
#patch -p0 -i files/remove-luci-uhttpd.patch
#git apply files/Kmemleak.patch
#rm target/linux/generic/pending-5.4/761-net-dsa-mt7530-Support-EEE-features.patch

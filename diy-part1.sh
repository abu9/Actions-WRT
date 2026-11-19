#!/bin/bash
#
# Copyright (c) 2019-2020 P3TERX <https://p3terx.com>
#
# This is free software, licensed under the MIT License.
# See /LICENSE for more information.
#
# https://github.com/P3TERX/Actions-OpenWrt
# File name: diy-part1.sh
# Description: OpenWrt DIY script part 1 (Before Update feeds)
#

# Uncomment a feed source
# sed -i 's/^#\(.*helloworld\)/\1/' feeds.conf.default

# Add a feed source
# echo 'src-git kenzojell https://github.com/kenzok8/jell' >>feeds.conf.default

# Function to handle file conflicts
function handle_conflict() {
    local localdir="$1"
    if [ -d "$localdir" ]; then
        if [ "$OVERWRITE_ALL" == "1" ]; then
            rm -rf "$localdir"
        elif [ "$SKIP_ALL" == "1" ]; then
            echo "Skipping $localdir"
            return 1
        else
            echo -n "Directory $localdir already exists. Overwrite? [Y] Yes  [A] Yes to All  [N] No  [L] No to All  [S] Suspend  [?] Help (default is \"Y\"): "
            read -r choice
            case "$choice" in
                Y|y ) rm -rf "$localdir" ;;
                N|n ) echo "Skipping $localdir"; return 1 ;;
                A|a ) rm -rf "$localdir"; export OVERWRITE_ALL=1 ;;
                L|l ) echo "Skipping $localdir"; export SKIP_ALL=1; return 1 ;;
                S|s ) echo "Suspending script"; exit 1 ;;
                * ) echo "Invalid choice"; return 1 ;;
            esac
        fi
    fi
}

# Function to clone a git repository sparsely with conflict checking
function git_sparse_clone() {
    local branch="$1"
    local rurl="$2"
    local localdir="$3"
    local tempdir="${localdir}_temp"
    shift 3
    handle_conflict "$localdir" || return 1
    echo "Cloning into temporary directory $tempdir"
    git clone -b "$branch" --depth 1 --filter=blob:none --sparse "$rurl" "$tempdir" || { echo "Clone failed"; rm -rf "$tempdir"; return 1; }
    (
        cd "$tempdir" || { echo "Failed to enter directory $tempdir"; rm -rf "$tempdir"; return 1; }
        git sparse-checkout init --cone
        git sparse-checkout set "$@"
        mkdir -p "../$localdir"
        for path in "$@"; do
            mkdir -p "../$localdir/$(dirname "$path")"
            mv -n "$path" "../$localdir/$path"
            # Update Makefile include paths if Makefile exists
            if [ -f "../$localdir/$path/Makefile" ]; then
                # shellcheck disable=SC2016
                sed -i 's|include ../../luci.mk|include $(TOPDIR)/feeds/luci/luci.mk|' "../$localdir/$path/Makefile"
            fi
        done
    )
    echo "Cleaning up temporary directory $tempdir"
    rm -rf "$tempdir"
}

# Function to clone a git repository with depth 1 with conflict checking
function git_clone() {
    local localdir=$(basename "$1" .git)
    handle_conflict "$localdir" || return 1
    git clone --depth 1 --filter=blob:none "$1"
}

# Define custom feed path
custom_feed_path="$PWD/customfeed"

# Add custom feed source to feeds.conf.default if it doesn't already exist
if ! grep -q "src-link custom $custom_feed_path" feeds.conf.default; then
    sed -i "1isrc-link custom $custom_feed_path" feeds.conf.default
fi

# Create custom feed directory and navigate into it
mkdir -p "$custom_feed_path" && cd "$custom_feed_path" || exit

# Clone specific packages and luci applications sparsely
git_sparse_clone master "https://github.com/immortalwrt/packages" "main_pkgs" lang/perl # fix perl compile issue with external-toolchain
git_sparse_clone master "https://github.com/immortalwrt/luci" "main_luci" applications/luci-app-filemanager

# Clone additional luci applications
git_clone https://github.com/tty228/luci-app-wechatpush 
git_clone https://github.com/stevenjoezhang/luci-app-adguardhome
git_clone https://github.com/gSpotx2f/luci-app-log

# Apply specific patches based on the repository URL
case $REPO_URL in
    immortalwrt/immortalwrt)
        git apply "${GITHUB_WORKSPACE}/patchs/0001-fix-default-settings-depends.patch"
        git_sparse_clone master "https://github.com/openwrt/packages" "officialpkgs" libs/libffi # fix libffi compile issue with external-toolchain
        ;;
esac

"""设备管理模块

本模块负责小米音箱设备的管理，包括：
- 设备列表管理
- 设备分组管理
- 设备信息查询
"""

from typing import TYPE_CHECKING, Optional

from xiaomusic.device_player import XiaoMusicDevice
from xiaomusic.utils.text_utils import parse_str_to_dict

if TYPE_CHECKING:
    from xiaomusic.xiaomusic import XiaoMusic


class DeviceManager:
    """设备管理器

    负责管理小米音箱设备列表、分组和设备信息查询。
    """

    def __init__(self, config, log, xiaomusic: Optional["XiaoMusic"] = None):
        """初始化设备管理器

        Args:
            config: 配置对象
            log: 日志对象
            xiaomusic: XiaoMusic实例（可选，用于延迟设置）
        """
        self.config = config
        self.log = log
        self.xiaomusic = xiaomusic

        # 设备相关数据结构
        self.devices: dict[str, XiaoMusicDevice] = {}
        self.device_id_did = {}  # device_id 到 did 的映射
        self.groups = {}  # 设备分组，key 为组名，value 为 device_id 列表

    def _update_devices(self, devices=None):
        """根据稳定配置原子构建并提交 runtime 设备映射。"""
        old_devices = self.devices
        source_devices = self.config.devices if devices is None else devices
        candidate_devices = {}
        candidate_device_id_did = {}
        candidate_groups = {}
        reused = []
        replaced = []

        try:
            did2group = parse_str_to_dict(self.config.group_list, d1=",", d2=":")
            for did, device in source_devices.items():
                device_id = device.device_id
                if not did or not device_id:
                    raise ValueError(f"invalid device configuration for {did!r}")
                group_name = did2group.get(did) or device.name
                if device_id in candidate_device_id_did:
                    raise ValueError(f"duplicate device_id: {device_id}")
                candidate_device_id_did[device_id] = did
                candidate_groups.setdefault(group_name, []).append(device_id)

                prev = old_devices.get(did)
                if prev is not None and getattr(prev.device, "device_id", None) == device_id:
                    candidate_devices[did] = prev
                    reused.append((prev, device, group_name))
                else:
                    if prev is not None:
                        replaced.append(prev)
                    candidate_devices[did] = XiaoMusicDevice(self.xiaomusic, device, group_name)
        except Exception as exc:
            logger = getattr(self.log, "exception", self.log.warning)
            logger(f"设备列表候选构建失败，保留旧 runtime: {exc}")
            raise

        # All constructors and candidate validation succeeded: update reusable players
        # without rebuilding playlists or touching playback/task state.
        for player, device, group_name in reused:
            player.device = device
            player.group_name = group_name
            player.config = self.config

        self.devices = candidate_devices
        self.device_id_did = candidate_device_id_did
        self.groups = candidate_groups

        removed = set(old_devices) - set(candidate_devices)
        stale_players = [old_devices[did] for did in removed] + replaced
        for player in stale_players:
            try:
                player.cancel_all_timer()
            except Exception as exc:
                self.log.warning(f"清理旧设备 timer 失败，继续清理后续设备: {exc}")

        self.log.info(f"设备列表已更新: device_id_did={self.device_id_did}")
        self.log.info(f"设备分组已更新: groups={self.groups}")

    def get_did(self, device_id):
        """根据device_id获取did

        Args:
            device_id: 设备ID

        Returns:
            str: 设备的did，如果不存在则返回空字符串
        """
        return self.device_id_did.get(device_id, "")

    def get_hardward(self, device_id):
        """获取设备硬件信息

        Args:
            device_id: 设备ID

        Returns:
            str: 设备的硬件型号，如果设备不存在则返回空字符串
        """
        device = self.get_device_by_device_id(device_id)
        if not device:
            return ""
        return device.hardware

    def get_device_by_device_id(self, device_id):
        """根据device_id获取设备配置

        Args:
            device_id: 设备ID

        Returns:
            Device: 设备配置对象，如果不存在则返回None
        """
        did = self.device_id_did.get(device_id)
        if not did:
            return None
        return self.config.devices.get(did)

    def get_group_device_id_list(self, group_name):
        """获取分组的设备ID列表

        Args:
            group_name: 分组名称

        Returns:
            list: 设备ID列表
        """
        return self.groups.get(group_name, [])

    def get_group_devices(self, group_name):
        """获取分组的设备字典

        Args:
            group_name: 分组名称

        Returns:
            dict: 设备字典，key为did，value为XiaoMusicDevice实例
        """
        device_id_list = self.groups.get(group_name, [])
        devices = {}
        for device_id in device_id_list:
            did = self.device_id_did.get(device_id, "")
            if did and did in self.devices:
                devices[did] = self.devices[did]
        return devices

    async def update_device_info(self, auth_manager):
        """更新设备信息并刷新设备列表

        从认证管理器获取最新的设备信息，然后更新设备列表。

        Args:
            auth_manager: 认证管理器实例

        Returns:
            bool: 是否成功完成设备信息刷新。
        """
        old_config_devices = self.config.devices
        old_cur_did = getattr(auth_manager, "_cur_did", None)
        try:
            result = await auth_manager.try_update_device_id()
            if result is None:
                if hasattr(auth_manager, "_cur_did"):
                    auth_manager._cur_did = old_cur_did
                return False
            self._update_devices(result)
            self.config.devices = result
            return True
        except Exception as exc:
            self.config.devices = old_config_devices
            if hasattr(auth_manager, "_cur_did"):
                auth_manager._cur_did = old_cur_did
            self.log.warning(f"更新设备信息失败，已回滚 config/runtime 事务: {exc}")
            return False

    def set_devices(self, devices: dict[str, XiaoMusicDevice]):
        """设置设备实例字典

        这个方法用于在主类中设置实际的设备实例。

        Args:
            devices: 设备实例字典，key为did，value为XiaoMusicDevice实例
        """
        self.devices = devices

import json
from repeatfs.plugins.distributed.utils.logger.basicLogger import Logger
from repeatfs.plugins.distributed.utils.config.netman import NetmanConfig
from repeatfs.plugins.distributed.utils.config.general import GeneralConfig


class Config:
    def __init__(self, file_path: str = None):
        self.file_path = file_path
        self.load_config(file_path)
        self.netman = self.config["netman"]
        self.general = self.config["general"]

    @staticmethod
    def make_default_config():
        return {
            "netman": NetmanConfig.make_default_config(),
            "general": GeneralConfig.make_default_config(),
        }

    def load_config(self, file_path: str = None):
        self.config = Config.make_default_config()
        if file_path is None:
            Logger.warning("No config file path provided, using default config")
            return
        if file_path:
            try:
                with open(file_path + "config.json", "r") as f:
                    file_config = json.load(f)
                self.update_config(file_config)
                self.load_sub_configs(file_path)
            except Exception as e:
                Logger.error(f"Error loading config file, using default config: {e}")

    def load_sub_configs(self, file_path: str = None):
        self.netman = NetmanConfig(file_path + "netman.json").config
        self.config["netman"] = self.netman

        self.general = GeneralConfig(file_path + "general.json").config
        self.config["general"] = self.general

    def update_sub_config(self, config, file_config):
        for key, value in file_config.items():
            if key in config:
                config[key] = value

    def update_config(self, file_config):
        for key, value in file_config.items():
            if isinstance(value, dict) and key in self.config:
                self.update_sub_config(self.config[key], value)
            elif key in self.config:
                self.config[key] = value
            self.netman = self.config["netman"]
            self.general = self.config["general"]

    def save_config(self, file_path):
        with open(file_path + "config.json", "w") as f:
            json.dump(self.config, f, indent=4)

        nested_config = {"netman": self.netman}
        NetmanConfig.save_config(
            file_path + "netman.json",
            NetmanConfig.get_config_from_string(json.dumps(nested_config)),
        )


        nested_config = {"general": self.general}
        GeneralConfig.save_config(
            file_path + "general.json",
            GeneralConfig.get_config_from_string(json.dumps(nested_config)),
        )

    def __str__(self):
        return json.dumps(self.config, indent=4)

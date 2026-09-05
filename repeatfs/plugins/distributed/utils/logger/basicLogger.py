import logging
import inspect
import os
import traceback


class Logger:
    logger = logging.getLogger("default_logger")
    logger.setLevel(logging.INFO)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s - %(levelname)s - [%(caller)s] - %(message)s"
    )
    console_handler.setFormatter(formatter)

    logger.addHandler(console_handler)

    print_stack_mode = False

    DEBUG = logging.DEBUG
    INFO = logging.INFO
    WARNING = logging.WARNING
    ERROR = logging.ERROR
    CRITICAL = logging.CRITICAL

    @staticmethod
    def set_level_from_string(str_level: str):
        # bydefault, the logger level should be info
        level = Logger.INFO
        # update level
        if str_level == "debug":
            level = Logger.DEBUG
        if str_level == "warning":
            level = Logger.WARNING
        if str_level == "error":
            level = Logger.ERROR
        if str_level == "critical":
            level = Logger.CRITICAL

        Logger.set_level(level)

    @staticmethod
    def set_level(level):
        # Set the logger level based on the input
        Logger.logger.setLevel(level)

        # Set the console handler level to match the logger level
        Logger.console_handler.setLevel(level)

        # Additionally, control the output based on the level set
        if level <= logging.DEBUG:
            # Log everything if the level is DEBUG or lower (more detailed)
            Logger.console_handler.setLevel(logging.DEBUG)
        elif level <= logging.INFO:
            # Log INFO and higher if the level is INFO
            Logger.console_handler.setLevel(logging.INFO)
        elif level <= logging.WARNING:
            # Log WARNING and higher if the level is WARNING
            Logger.console_handler.setLevel(logging.WARNING)
        elif level <= logging.ERROR:
            # Log ERROR and higher if the level is ERROR
            Logger.console_handler.setLevel(logging.ERROR)
        else:
            # Only log CRITICAL if the level is CRITICAL
            Logger.console_handler.setLevel(logging.CRITICAL)

    @staticmethod
    def log(level, msg, *args, **kwargs):
        caller_info = Logger._get_caller_info()
        extra = {"caller": caller_info}
        Logger.logger.log(level, msg, *args, extra=extra, **kwargs)

    @staticmethod
    def _get_caller_info():
        frame = inspect.currentframe()
        # Skip specific stack frames to find the actual caller outside this logger
        while frame:
            if frame.f_code.co_name in [
                "log",
                "_get_caller_info",
                "debug",
                "info",
                "warning",
                "error",
                "critical",
            ]:
                frame = frame.f_back
            else:
                break  # Found the caller

        frame_info = inspect.getframeinfo(frame)
        filename = os.path.basename(frame_info.filename)
        lineno = frame_info.lineno
        return f"{filename}:{lineno}"

    @staticmethod
    def debug(msg):
        Logger.log(logging.DEBUG, msg)

    @staticmethod
    def info(msg):
        Logger.log(logging.INFO, msg)

    @staticmethod
    def warning(msg):
        Logger.log(logging.WARNING, msg)

    @staticmethod
    def error(msg):
        if Logger.print_stack_mode:
            stack_info = traceback.format_stack()
            formatted_stack_info = "".join(stack_info)
            full_msg = f"{msg}\nStack trace:\n{formatted_stack_info}"
            Logger.log(logging.ERROR, full_msg)
        else:
            Logger.log(logging.ERROR, msg)

    @staticmethod
    def critical(msg):
        Logger.log(logging.CRITICAL, msg)

    def init_publish(self) -> None:
        for logger in self.created_loggers.values():
            logger.init_publish()

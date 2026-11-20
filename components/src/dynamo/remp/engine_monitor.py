                await self.engine_client.do_log_stats()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.debug("Error in periodic stats logging", exc_info=True)

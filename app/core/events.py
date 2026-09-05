import logging
from typing import Any, Callable, Dict, List

logger = logging.getLogger("dealflow360.events")


class EventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[Callable[..., Any]]] = {}

    def subscribe(self, event_name: str, handler: Callable[..., Any]) -> None:
        if event_name not in self._handlers:
            self._handlers[event_name] = []
        if handler not in self._handlers[event_name]:
            self._handlers[event_name].append(handler)
            logger.debug(f"Subscribed {handler.__name__} to event: {event_name}")

    def emit(self, event_name: str, payload: Dict[str, Any], session: Any = None) -> None:
        logger.info(f"Event emitted: {event_name}")
        handlers = self._handlers.get(event_name, [])
        for handler in handlers:
            try:
                if session is not None:
                    handler(payload=payload, session=session)
                else:
                    handler(payload=payload)
            except Exception as e:
                logger.error(f"Error in handler {handler.__name__} for event {event_name}: {e}", exc_info=True)
                raise e


# Global in-process event bus instance
event_bus = EventBus()

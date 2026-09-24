"""Provider adapters for external services. Business code never imports a vendor: it calls a service, the service calls an interface
defined in `base.py`, and an adapter in this package (chosen by the shop's configuration) talks to the outside world."""

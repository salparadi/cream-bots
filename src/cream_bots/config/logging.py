import logging

def logger(name):
	"""Set up and return a logger with the specified name."""
	# Create a logger
	logger = logging.getLogger(name)
	logger.setLevel(logging.INFO)  # Or whatever default level you want

	# Create console handler and set level
	ch = logging.StreamHandler()
	ch.setLevel(logging.INFO)  # Or another level

	# Create formatter
	formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

	# Add formatter to ch
	ch.setFormatter(formatter)

	# Add ch to logger
	logger.addHandler(ch)

	return logger
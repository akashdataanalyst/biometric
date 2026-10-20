app_name = "biometric_integration"
app_title = "Biometric Integration"
app_publisher = "Calco"
app_description = "Universal biometric / punching machine integration for ERPNext HRMS"
app_email = "mis.1@calco.in"
app_license = "MIT"

required_apps = ["hrms"]

# Tile on the apps screen (/apps); the desk menu is the module sidebar
# (biometric_integration/sidebar/biometric_integration/biometric_integration.json).
add_to_apps_screen = [
	{
		"name": "biometric_integration",
		"logo": "/assets/biometric_integration/images/biometric.svg",
		"title": "Biometric",
		"route": "/desk/biometric-live-monitor",
		"has_permission": "biometric_integration.install.has_app_permission",
	}
]

app_include_js = ["/assets/biometric_integration/js/biometric.js"]

# Devices speaking the ADMS / iclock push protocol (eSSL, ZKTeco, Identix ...)
# call fixed paths like /iclock/cdata. This renderer answers those paths
# directly, so no nginx rewrite is needed.
page_renderer = ["biometric_integration.route_renderer.BiometricRouteRenderer"]

after_install = "biometric_integration.install.after_install"
after_migrate = "biometric_integration.install.after_migrate"

scheduler_events = {
	"cron": {
		"*/5 * * * *": ["biometric_integration.tasks.every_five_minutes"],
	},
	"daily": ["biometric_integration.tasks.daily"],
}

doc_events = {
	"Employee": {
		"on_update": "biometric_integration.engine.on_employee_update",
	}
}

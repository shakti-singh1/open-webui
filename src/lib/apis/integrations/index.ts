import { WEBUI_API_BASE_URL } from '$lib/constants';

// ─── Status ──────────────────────────────────────────────────────────────────

export const getIntegrationStatus = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/integrations/status`, {
		headers: { Authorization: `Bearer ${token}` }
	})
		.then(async (r) => {
			if (!r.ok) throw await r.json();
			return r.json();
		})
		.catch((err) => {
			console.error(err);
			return null;
		});
	return res;
};

// ─── Microsoft ────────────────────────────────────────────────────────────────

/**
 * Opens a popup window for Microsoft OAuth consent.
 * Returns a Promise that resolves once the popup is closed.
 */
export const connectMicrosoftPopup = (): Promise<void> => {
	return new Promise((resolve) => {
		const url = `${WEBUI_API_BASE_URL}/integrations/microsoft/connect`;
		const popup = window.open(url, 'microsoft_oauth', 'width=520,height=680,resizable=yes,scrollbars=yes');

		const timer = setInterval(() => {
			if (!popup || popup.closed) {
				clearInterval(timer);
				resolve();
			}
		}, 500);
	});
};

export const disconnectMicrosoft = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/integrations/microsoft/disconnect`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${token}` }
	})
		.then(async (r) => {
			if (!r.ok) throw await r.json();
			return r.json();
		})
		.catch((err) => {
			console.error(err);
			return null;
		});
	return res;
};

// ─── Slack ────────────────────────────────────────────────────────────────────

/**
 * Opens a popup window for Slack OAuth consent.
 * Returns a Promise that resolves once the popup is closed.
 */
export const connectSlackPopup = (): Promise<void> => {
	return new Promise((resolve) => {
		const url = `${WEBUI_API_BASE_URL}/integrations/slack/connect`;
		const popup = window.open(url, 'slack_oauth', 'width=520,height=680,resizable=yes,scrollbars=yes');

		const timer = setInterval(() => {
			if (!popup || popup.closed) {
				clearInterval(timer);
				resolve();
			}
		}, 500);
	});
};

export const disconnectSlack = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/integrations/slack/disconnect`, {
		method: 'DELETE',
		headers: { Authorization: `Bearer ${token}` }
	})
		.then(async (r) => {
			if (!r.ok) throw await r.json();
			return r.json();
		})
		.catch((err) => {
			console.error(err);
			return null;
		});
	return res;
};

// ─── Admin Config ─────────────────────────────────────────────────────────────

export const getTeamIntegrationsConfig = async (token: string) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/team-integrations`, {
		headers: { Authorization: `Bearer ${token}` }
	})
		.then(async (r) => {
			if (!r.ok) throw await r.json();
			return r.json();
		})
		.catch((err) => {
			console.error(err);
			return null;
		});
	return res;
};

export const setTeamIntegrationsConfig = async (token: string, config: object) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/configs/team-integrations`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify(config)
	})
		.then(async (r) => {
			if (!r.ok) throw await r.json();
			return r.json();
		})
		.catch((err) => {
			console.error(err);
			return null;
		});
	return res;
};

frappe.provide("frappe.ui");
frappe.provide("frappe.views");

frappe.views.EmailTemplateSelector = class EmailTemplateSelector {
	constructor(callback, frm) {
		this.callback = callback;
		this.frm = frm;
		this.templates = [];
		this.categories_data = [];
		this.selected_template = null;
		this.dialog = null;
		this.make();
	}

	get_categories() {
		const static_categories = [
			{ id: "all", label: __("All"), icon: "folder-normal" },
			{ id: "favorites", label: __("Favorites"), icon: "star" },
		];

		const dynamic_categories = this.categories_data.map((cat) => ({
			id: cat.name,
			label: cat.name,
			icon: cat.icon || "tag",
		}));

		return [...static_categories, ...dynamic_categories];
	}

	make() {
		this.dialog = new frappe.ui.Dialog({
			title: __("Select Email Template"),
			size: "extra-large",
			primary_action_label: __("Insert"),
			primary_action: () => {
				if (this.selected_template && this.callback) {
					this.callback(this.selected_template);
					this.dialog.hide();
				}
			},
			secondary_action_label: __("Cancel"),
			secondary_action: () => {
				this.dialog.hide();
			},
		});

		this.render_dialog_body();
		this.dialog.show();

		this.dialog.$wrapper.find(".modal-dialog").addClass("email-template-modal");

		this.bind_events();
		this.load_data();
	}

	render_dialog_body() {
		const body_html = `
			<div class="template-manager-body">
				<div class="tm-sidebar" id="tm-sidebar"></div>

				<div class="tm-main">
					<div class="tm-toolbar">
						<div class="tm-search-wrapper">
							<span class="search-icon">${frappe.utils.icon("search", "sm")}</span>
							<input type="text" class="tm-search-bar" placeholder="${__("Search templates...")}">
						</div>
					</div>
					<div class="tm-grid" id="tm-grid">
						<div class="text-muted text-center" style="padding-top: 50px;">
							${__("Loading...")}
						</div>
					</div>
				</div>

				<div class="tm-preview-pane">
					<div class="tm-preview-header">
						<span id="tm-preview-title">${__("Preview")}</span>
					</div>
					<div class="tm-preview-frame" id="tm-preview-content">
						<div class="tm-empty-state">
							${frappe.utils.icon("preview", "xl")}
							<p>${__("Select a template to view preview")}</p>
						</div>
					</div>
				</div>
			</div>
		`;

		this.dialog.$wrapper.find(".modal-body").css("padding", "0").html(body_html);
	}

	load_data() {
		const templates_promise = frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Email Template",
				filters: {
					restrict_to_doctype: ["in", ["", this.frm ? this.frm.doctype : ""]],
				},
				fields: [
					"name",
					"subject",
					"response",
					"response_html",
					"use_html",
					"_liked_by",
					"email_template_category",
				],
				limit_page_length: 100,
			},
		});

		const categories_promise = frappe.call({
			method: "frappe.client.get_list",
			args: {
				doctype: "Email Template Category",
				fields: ["name", "icon"],
				order_by: "name asc",
				limit_page_length: 0,
			},
		});

		Promise.all([templates_promise, categories_promise]).then(
			([r_templates, r_categories]) => {
				if (r_templates.message) {
					this.templates = r_templates.message;
				}
				if (r_categories.message) {
					this.categories_data = r_categories.message;
				}
				this.render_categories("all");
				this.render_grid(this.templates);
			}
		);
	}

	is_liked_by_user(tpl) {
		if (!tpl._liked_by) return false;
		try {
			const likes = JSON.parse(tpl._liked_by);
			return Array.isArray(likes) && likes.includes(frappe.session.user);
		} catch (e) {
			return tpl._liked_by.includes(frappe.session.user);
		}
	}

	toggle_like(tpl, $icon) {
		const is_liked = $icon.hasClass("liked");
		const action = is_liked ? "No" : "Yes";

		// Optimistic UI update
		if (is_liked) {
			$icon.removeClass("liked");
		} else {
			$icon.addClass("liked");
		}

		frappe.call({
			method: "frappe.desk.like.toggle_like",
			args: {
				doctype: "Email Template",
				name: tpl.name,
				add: action,
			},
			callback: (r) => {
				if (!r.exc) {
					let likes = [];
					try {
						likes = JSON.parse(tpl._liked_by || "[]");
					} catch (e) {
						console.warn(`Could not load likes`);
					}

					if (action === "Yes") {
						if (!likes.includes(frappe.session.user)) {
							likes.push(frappe.session.user);
						}
					} else {
						likes = likes.filter((u) => u !== frappe.session.user);
					}
					tpl._liked_by = JSON.stringify(likes);
				}
			},
		});
	}

	render_categories(active_id) {
		const $sidebar = this.dialog.$wrapper.find("#tm-sidebar");
		$sidebar.empty();

		const categories = this.get_categories();

		categories.forEach((cat) => {
			const $item = $(`
				<div class="tm-category-item ${cat.id === active_id ? "active" : ""}" data-id="${cat.id}">
					<span class="category-icon">${frappe.utils.icon(cat.icon)}</span>
					<span class="category-label">${cat.label}</span>
				</div>
			`);

			$item.click(() => {
				this.render_categories(cat.id);
				this.filter_templates_by_category(cat.id);
			});
			$sidebar.append($item);
		});
	}

	filter_templates_by_category(category_id) {
		let filtered = [];

		if (category_id === "all") {
			filtered = this.templates;
		} else if (category_id === "favorites") {
			filtered = this.templates.filter((t) => this.is_liked_by_user(t));
		} else {
			filtered = this.templates.filter((t) => t.email_template_category === category_id);
		}

		this.render_grid(filtered);
	}

	render_grid(templates) {
		const $grid = this.dialog.$wrapper.find("#tm-grid");
		$grid.empty();

		if (!templates || templates.length === 0) {
			$grid.html(`
				<div class="tm-no-results">
					${frappe.utils.icon("search", "lg")}
					<p>${__("No templates found")}</p>
				</div>
			`);
			return;
		}

		templates.forEach((tpl) => {
			const plainText = tpl.use_html
				? tpl.response_html.substring(0, 150)
				: $(tpl.response).text().substring(0, 150);
			const isLiked = this.is_liked_by_user(tpl);

			const $card = $(`
				<div class="tm-card" data-name="${tpl.name}">
					<div class="tm-card-body">
						<div class="tm-heart-icon ${isLiked ? "liked" : ""}" title="${__("Toggle Favorite")}">
							${frappe.utils.icon("heart", "sm")}
						</div>
						<div class="tm-card-mini-preview">
							${plainText}
							<div class="tm-card-overlay"></div>
						</div>
					</div>
					<div class="tm-card-footer">
						<div class="tm-card-title" title="${tpl.name}">${tpl.name}</div>
					</div>
				</div>
			`);

			// Event Listener pour le coeur
			$card.find(".tm-heart-icon").click((e) => {
				e.stopPropagation();
				this.toggle_like(tpl, $(e.currentTarget));
			});

			// Event Listener pour la carte
			$card.click(() => {
				this.dialog.$wrapper.find(".tm-card").removeClass("selected");
				$card.addClass("selected");
				this.selected_template = tpl;
				this.update_preview(tpl);
			});

			$grid.append($card);
		});
	}

	update_preview(tpl) {
		this.dialog.$wrapper.find("#tm-preview-title").text(tpl.name);
		const $content = this.dialog.$wrapper.find("#tm-preview-content");

		// État de chargement
		$content.html(`
			<div class="flex flex-column align-center justify-center h-100 text-muted">
				<span class="mb-2">${frappe.utils.icon("refresh", "animate-spin")}</span>
				<span>${__("Rendering preview...")}</span>
			</div>
		`);

		// Appel API pour obtenir le rendu Jinja contextuel
		frappe.call({
			method: "frappe.email.doctype.email_template.email_template.get_email_template",
			args: {
				template_name: tpl.name,
				doc: this.frm ? this.frm.doc : {}, // On passe le document actuel pour le contexte
				_lang: this.frm ? this.frm.doc.language : frappe.boot.lang,
			},
			callback: (r) => {
				if (r.message) {
					// r.message contient { subject: "...", message: "..." }
					// On met à jour l'objet template sélectionné avec le contenu rendu
					// pour qu'au moment de l'insertion ("Insert"), le texte soit déjà traité
					this.selected_template.subject = r.message.subject;
					this.selected_template.response = r.message.message;

					$content.html(r.message.message);
				}
			},
			error: (e) => {
				$content.html(`<div class="text-danger">${__("Error rendering template")}</div>`);
			},
		});
	}

	bind_events() {
		this.dialog.$wrapper.find(".tm-search-bar").on("input", (e) => {
			const val = e.target.value.toLowerCase();
			const filtered = this.templates.filter(
				(t) => t.name.toLowerCase().includes(val) || t.subject.toLowerCase().includes(val)
			);
			this.render_grid(filtered);
		});
	}
};

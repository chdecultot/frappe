frappe.provide("frappe.ui");
frappe.provide("frappe.views");

frappe.views.EmailTemplateSelector = class EmailTemplateSelector {
	constructor(callback, communication_composer) {
		this.callback = callback;
		this.communication_composer = communication_composer;
		this.frm = communication_composer.frm || {};
		this.templates = [];
		this.categories_data = [];
		this.selected_template = null;
		this.editing_template = null;
		this.dialog = null;
		this.can_write = frappe.model.can_write("Email Template");
		this.current_view = "select";

		this.communication_composer.dialog.is_minimized = false;
		this.communication_composer.dialog.toggle_minimize();

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
			title: __("Email Templates"),
			size: "extra-large",
			onhide: () => {
				if (
					this.communication_composer &&
					this.communication_composer.dialog &&
					this.communication_composer.dialog.is_minimized
				) {
					this.communication_composer.dialog.toggle_minimize();
				}
			},
		});

		this.render_dialog_body();
		this.dialog.show();

		this.dialog.$wrapper.find(".modal-dialog").addClass("email-template-modal");

		this.dialog.footer.hide();

		this.bind_events();
		this.load_data();
		this.switch_view("select");
	}

	render_dialog_body() {
		const body_html = `
			<div class="template-manager-body">
				<div class="tm-sidebar" id="tm-sidebar"></div>

				<div class="tm-main">
					<div class="tm-toolbar" id="tm-toolbar"></div>

					<div class="tm-content-wrapper">
						<div id="tm-grid-view">
							<div class="tm-grid" id="tm-grid">
								<div class="text-muted text-center" style="padding-top: 50px;">
									${__("Loading...")}
								</div>
							</div>
						</div>

						<div id="tm-editor-view">
							<div id="tm-edit-form-container"></div>
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
					<div class="flex" style="margin-top: auto; padding-top: 15px; border-top: 1px solid var(--border-color); gap: 10px;">
						<button class="btn btn-default" style="flex: 1;" id="btn-insert-template" disabled>
							${__("Insert Template")}
						 </button>
						 <button class="btn btn-primary" style="flex: 1;" id="btn-clear-insert-template" disabled>
							${__("Clear & Insert Template")}
						 </button>
					</div>
				</div>
			</div>
		`;

		this.dialog.$wrapper.find(".modal-body").css("padding", "0").html(body_html);
	}

	switch_view(view) {
		this.current_view = view;
		const $main = this.dialog.$wrapper.find(".tm-main");

		this.update_toolbar(view);

		if (view === "select") {
			$main.find("#tm-grid-view").show();
			$main.find("#tm-editor-view").hide();
			this.dialog.$wrapper
				.find("#btn-insert-template, #btn-clear-insert-template")
				.toggle(true);
		} else {
			$main.find("#tm-grid-view").hide();
			$main.find("#tm-editor-view").show();
		}
	}

	update_toolbar(view) {
		const $toolbar = this.dialog.$wrapper.find("#tm-toolbar");
		$toolbar.empty();

		if (view === "select") {
			$toolbar.html(`
				<div class="tm-search-wrapper" style="flex:1; margin-right: 15px;">
					<span class="search-icon">${frappe.utils.icon("search", "sm")}</span>
					<input type="text" class="tm-search-bar" placeholder="${__("Search templates...")}">
				</div>
				${
					this.can_write
						? `
					<button class="btn btn-default btn-sm" id="btn-new-template">
						${frappe.utils.icon("add", "sm")} ${__("New")}
					</button>
				`
						: ""
				}
			`);

			$toolbar.find(".tm-search-bar").on("input", (e) => {
				const val = e.target.value.toLowerCase();
				const filtered = this.templates.filter(
					(t) =>
						t.name.toLowerCase().includes(val) || t.subject.toLowerCase().includes(val)
				);
				this.render_grid(filtered);
			});
		} else {
			const title = this.editing_template ? this.editing_template.name : __("New Template");
			$toolbar.html(`
				<div class="flex align-center">
					<button class="btn btn-sm btn-link-gray" id="btn-back-to-grid" style="margin-right: 10px;">
						${frappe.utils.icon("arrow-left", "sm")}
					</button>
					<h5 style="margin:0; font-weight:600; max-width: 300px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis;">${title}</h5>
				</div>
				<div class="flex align-center">
					<span class="text-muted text-small mr-2" id="save-status"></span>
					<button class="btn btn-primary btn-sm" id="btn-save-template">
						${frappe.utils.icon("save", "sm")} ${__("Save")}
					</button>
				</div>
			`);
		}
	}

	setup_edit_form(template = null) {
		this.editing_template = template;
		const is_new = !template;

		const container = this.dialog.$wrapper.find("#tm-edit-form-container");
		container.empty();

		const fields = [
			{
				label: __("Name"),
				fieldname: "name",
				fieldtype: "Data",
				reqd: 1,
				read_only: !is_new,
				default: is_new ? "" : template.name,
			},
			{
				label: __("Subject"),
				fieldname: "subject",
				fieldtype: "Data",
				reqd: 1,
				default: is_new ? "" : template.subject,
			},
			{
				label: __("Category"),
				fieldname: "email_template_category",
				fieldtype: "Link",
				options: "Email Template Category",
				default: is_new ? "" : template.email_template_category,
			},
			{
				label: __("Restrict to DocType"),
				fieldname: "restrict_to_doctype",
				fieldtype: "Link",
				options: "DocType",
				default: is_new
					? this.frm
						? this.frm.doctype
						: ""
					: template.restrict_to_doctype,
			},
			{ fieldtype: "Section Break" },
			{
				label: __("Use HTML"),
				fieldname: "use_html",
				fieldtype: "Check",
				default: is_new ? 0 : template.use_html,
				onchange: () => {
					const use_html = this.edit_form.get_value("use_html");
					this.edit_form.set_df_property("response", "hidden", use_html);
					this.edit_form.set_df_property("response", "reqd", !use_html);
					this.edit_form.set_df_property("response_html", "hidden", !use_html);
					this.edit_form.set_df_property("response_html", "reqd", use_html);
				},
			},
			{
				label: __("Response (Text)"),
				fieldname: "response",
				fieldtype: "Text Editor",
				depends_on: "eval:!doc.use_html",
				default: is_new ? "" : template.response,
			},
			{
				label: __("Response (HTML Code)"),
				fieldname: "response_html",
				fieldtype: "Code",
				options: "HTML",
				depends_on: "eval:doc.use_html",
				default: is_new ? "" : template.response_html,
			},
		];

		this.edit_form = new frappe.ui.FieldGroup({
			fields: fields,
			body: container,
		});
		this.edit_form.make();

		const use_html = is_new ? 0 : template.use_html;
		this.edit_form.set_df_property("response", "hidden", use_html);
		this.edit_form.set_df_property("response_html", "hidden", !use_html);
	}

	save_template() {
		const values = this.edit_form.get_values();
		if (!values) return;

		const $btn = this.dialog.$wrapper.find("#btn-save-template");
		const $status = this.dialog.$wrapper.find("#save-status");

		$btn.prop("disabled", true);
		$status.text(__("Saving..."));

		const is_new = !this.editing_template;
		const method =
			"frappe.email.doctype.email_template.email_template.create_update_email_template";

		let doc = { doctype: "Email Template", ...values };
		if (!is_new) doc.name = this.editing_template.name;

		frappe.call({
			method: method,
			args: { doc: doc },
			callback: (r) => {
				$btn.prop("disabled", false);
				$status.text("");

				if (!r.exc) {
					frappe.show_alert({ message: __("Saved"), indicator: "green" }, 3);

					this.editing_template = r.message;

					if (is_new) this.update_toolbar("edit");

					this.selected_template = this.editing_template;
					this.update_preview(this.editing_template);

					this.load_data(false);
				}
			},
		});
	}

	load_data(render = true) {
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
					"restrict_to_doctype",
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
				if (r_templates.message) this.templates = r_templates.message;
				if (r_categories.message) this.categories_data = r_categories.message;

				if (render) {
					this.render_categories("all");
					this.render_grid(this.templates);
				}
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

		if (is_liked) {
			$icon.removeClass("liked");
		} else {
			$icon.addClass("liked");
		}

		frappe.call({
			method: "frappe.desk.like.toggle_like",
			args: { doctype: "Email Template", name: tpl.name, add: action },
			callback: (r) => {
				if (!r.exc) {
					let likes = [];
					try {
						likes = JSON.parse(tpl._liked_by || "[]");
					} catch (e) {
						console.warn(e);
					}

					if (action === "Yes") {
						if (!likes.includes(frappe.session.user)) likes.push(frappe.session.user);
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
				? tpl.response_html
					? tpl.response_html.substring(0, 150)
					: ""
				: $(tpl.response).text().substring(0, 150);
			const isLiked = this.is_liked_by_user(tpl);

			const $card = $(`
				<div class="tm-card" data-name="${tpl.name}">
					<div class="tm-card-body">
						<div class="tm-actions-top">
							 <div class="tm-heart-icon ${isLiked ? "liked" : ""}" title="${__("Toggle Favorite")}">
								${frappe.utils.icon("heart", "sm")}
							</div>
							${
								this.can_write
									? `
							<div class="tm-edit-icon" title="${__("Edit Template")}">
								${frappe.utils.icon("edit", "sm")}
							</div>
							`
									: ""
							}
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

			$card.find(".tm-heart-icon").click((e) => {
				e.stopPropagation();
				this.toggle_like(tpl, $(e.currentTarget));
			});

			if (this.can_write) {
				$card.find(".tm-edit-icon").click((e) => {
					e.stopPropagation();
					this.setup_edit_form(tpl);
					this.switch_view("edit");

					this.selected_template = tpl;
					this.update_preview(tpl);
				});
			}

			$card.click(() => {
				this.dialog.$wrapper.find(".tm-card").removeClass("selected");
				$card.addClass("selected");
				this.selected_template = tpl;
				this.dialog.$wrapper
					.find("#btn-insert-template, #btn-clear-insert-template")
					.prop("disabled", false);
				this.update_preview(tpl);
			});

			$grid.append($card);
		});
	}

	update_preview(tpl) {
		this.dialog.$wrapper.find("#tm-preview-title").text(tpl.name);
		const $content = this.dialog.$wrapper.find("#tm-preview-content");

		$content.html(`
			<div class="flex flex-column align-center justify-center h-100 text-muted">
				<span class="mb-2">${frappe.utils.icon("refresh", "animate-spin")}</span>
				<span>${__("Rendering preview...")}</span>
			</div>
		`);

		frappe.call({
			method: "frappe.email.doctype.email_template.email_template.get_email_template",
			args: {
				template_name: tpl.name,
				doc: this.frm ? this.frm.doc : {},
			},
			callback: (r) => {
				if (r.message && !r.message.error) {
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
		this.dialog.$wrapper.on("click", "#btn-new-template", () => {
			this.setup_edit_form(null);
			this.switch_view("edit");
			this.dialog.$wrapper.find("#tm-preview-title").text(__("New Template"));
			this.dialog.$wrapper
				.find("#tm-preview-content")
				.html(
					'<div class="text-muted p-4 text-center">Fill the form to see preview...</div>'
				);
		});

		this.dialog.$wrapper.on("click", "#btn-back-to-grid", () => {
			this.switch_view("select");
		});

		this.dialog.$wrapper.on("click", "#btn-save-template", () => {
			this.save_template();
		});

		const handle_insert = (options) => {
			if (this.selected_template && this.callback) {
				this.callback(this.selected_template, options);
				this.dialog.hide();
			}
		};

		this.dialog.$wrapper.on("click", "#btn-insert-template", () =>
			handle_insert({ action: "insert" })
		);
		this.dialog.$wrapper.on("click", "#btn-clear-insert-template", () =>
			handle_insert({ action: "clear_insert" })
		);
	}
};

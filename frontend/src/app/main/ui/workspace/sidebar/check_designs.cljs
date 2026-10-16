;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns app.main.ui.workspace.sidebar.check-designs
  (:require-macros [app.main.style :as stl])
  (:require
   [app.common.data :as d]
   [app.common.uuid :as uuid]
   [app.main.data.workspace :as dw]
   [app.main.data.workspace.check-designs :as dwcd]
   [app.main.refs :as refs]
   [app.main.store :as st]
   [app.main.ui.ds.product.empty-state :refer [empty-state*]]
   [app.main.ui.ds.product.panel-title :refer [panel-title*]]
   [app.util.dom :as dom]
   [app.util.i18n :refer [tr] :as i18n]
   [rumext.v2 :as mf]))

(defn- kind-label
  [kind]
  (case kind
    :fill-color   (tr "workspace.check-designs.kind.fill-color")
    :stroke-color (tr "workspace.check-designs.kind.stroke-color")
    :typography   (tr "workspace.check-designs.kind.typography")
    nil))

(defn- shape-id-from-event
  [event]
  (some-> event dom/get-current-target (dom/get-data "shape-id") uuid/parse))

(mf/defc check-designs-content*
  [{:keys [class] :rest props}]
  (let [objects  (mf/deref refs/workspace-page-objects)
        findings (mf/with-memo [objects] (dwcd/check-page objects))
        total    (count findings)

        on-close
        (mf/use-fn
         #(st/emit! (dw/remove-layout-flag :check-designs)))

        on-select
        (mf/use-fn
         (fn [event]
           (when-let [shape-id (shape-id-from-event event)]
             (st/emit! (dw/select-shapes (d/ordered-set shape-id))
                       dw/zoom-to-selected-shape))))

        props
        (mf/spread-props props {:class [class (stl/css :check-designs)]})]

    [:> :div props
     [:> panel-title* {:class (stl/css :check-designs-title)
                       :text (tr "workspace.check-designs.title")
                       :on-close on-close}]

     (if (zero? total)
       [:div {:class (stl/css :check-designs-empty)}
        [:> empty-state* {:text (tr "workspace.check-designs.empty")}]]

       [:div {:class (stl/css :check-designs-content)}
        [:div {:class (stl/css :check-designs-count)}
         (tr "workspace.check-designs.count" (i18n/c total))]

        (for [{:keys [shape-id shape-name kind]} findings]
          [:button {:key (str shape-id "-" (name kind))
                    :type "button"
                    :class (stl/css :finding)
                    :data-shape-id (str shape-id)
                    :on-click on-select}
           [:span {:class (stl/css :finding-name)} shape-name]
           [:span {:class (stl/css :finding-kind)} (kind-label kind)]])])]))

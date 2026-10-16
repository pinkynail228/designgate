;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns app.main.ui.workspace.sidebar.version-compare
  (:require-macros [app.main.style :as stl])
  (:require
   [app.main.data.workspace.version-compare :as dwvc]
   [app.main.store :as st]
   [app.main.ui.ds.foundations.assets.icon :as i :refer [icon*]]
   [app.main.ui.ds.product.empty-state :refer [empty-state*]]
   [app.main.ui.ds.product.panel-title :refer [panel-title*]]
   [app.util.i18n :refer [tr]]
   [okulary.core :as l]
   [rumext.v2 :as mf]))

(def ^:private versions
  (l/derived :workspace-versions st/state))

(def ^:private sections
  [{:key :components
    :icon i/component
    :label "workspace.versions.compare.section.components"}
   {:key :colors
    :icon i/swatches
    :label "workspace.versions.compare.section.colors"}
   {:key :typographies
    :icon i/text-typography
    :label "workspace.versions.compare.section.typographies"}
   {:key :tokens
    :icon i/tokens
    :label "workspace.versions.compare.section.tokens"}])

(def ^:private marker-char
  {:added "+"
   :changed "~"
   :removed "−"})

(def ^:private marker-label
  {:added "workspace.versions.compare.marker.added"
   :changed "workspace.versions.compare.marker.changed"
   :removed "workspace.versions.compare.marker.removed"})

(mf/defc compare-row*
  {::mf/private true}
  [{:keys [item kind]}]
  [:li {:class (stl/css :compare-row)}
   [:span {:class (stl/css-case :compare-marker true
                                :compare-marker-added (= kind :added)
                                :compare-marker-changed (= kind :changed)
                                :compare-marker-removed (= kind :removed))
           :title (tr (get marker-label kind))
           :aria-hidden true}
    (get marker-char kind)]
   [:span {:class (stl/css :compare-name)} (:name item)]])

(mf/defc compare-section*
  {::mf/private true}
  [{:keys [icon label data]}]
  (let [open?   (mf/use-state true)
        added   (get data :added [])
        changed (get data :changed [])
        removed (get data :removed [])
        total   (+ (count added) (count changed) (count removed))

        on-toggle
        (mf/use-fn #(swap! open? not))]

    [:section {:class (stl/css :compare-section)}
     [:button {:type "button"
               :class (stl/css :compare-section-header)
               :aria-expanded @open?
               :on-click on-toggle}
      [:> icon* {:icon-id icon
                 :size "m"
                 :class (stl/css :compare-section-icon)}]
      [:span {:class (stl/css :compare-section-label)} label]
      [:span {:class (stl/css :compare-section-count)} (str total)]
      [:> icon* {:icon-id i/arrow
                 :size "m"
                 :class (stl/css-case :compare-section-arrow true
                                      :open @open?)}]]

     (when @open?
       (if (zero? total)
         [:div {:class (stl/css :compare-section-empty)}
          (tr "workspace.versions.compare.section-empty")]
         [:ul {:class (stl/css :compare-list)}
          (for [item added]
            [:> compare-row* {:key (str "added-" (:id item)) :item item :kind :added}])
          (for [item changed]
            [:> compare-row* {:key (str "changed-" (:id item)) :item item :kind :changed}])
          (for [item removed]
            [:> compare-row* {:key (str "removed-" (:id item)) :item item :kind :removed}])]))]))

(mf/defc version-compare*
  []
  (let [versions* (mf/deref versions)
        compare   (:compare versions*)
        data      (:data compare)

        on-close
        (mf/use-fn #(st/emit! (dwvc/clear-compare)))

        has-changes?
        (->> [:components :colors :typographies :tokens]
             (some (fn [section-key]
                     (let [section (get data section-key)]
                       (or (seq (:added section))
                           (seq (:changed section))
                           (seq (:removed section)))))))]

    [:div {:class (stl/css :compare-panel)}
     [:> panel-title* {:class (stl/css :compare-title)
                       :text (tr "workspace.versions.compare.title")
                       :on-close on-close}]

     (case (:status compare)
       :loading
       [:div {:class (stl/css :compare-state)}
        [:> empty-state* {:icon i/clock
                          :text (tr "workspace.versions.compare.loading")}]]

       :error
       [:div {:class (stl/css :compare-state)}
        [:> empty-state* {:icon i/status-wrong
                          :text (tr "workspace.versions.compare.error")}]]

       :loaded
       (if has-changes?
         [:div {:class (stl/css :compare-body)}
          (for [{section-key :key icon :icon label :label} sections]
            [:> compare-section* {:key (name section-key)
                                  :icon icon
                                  :label (tr label)
                                  :data (get data section-key)}])]
         [:div {:class (stl/css :compare-state)}
          [:> empty-state* {:icon i/status-tick
                            :text (tr "workspace.versions.compare.empty")}]])

       nil)]))

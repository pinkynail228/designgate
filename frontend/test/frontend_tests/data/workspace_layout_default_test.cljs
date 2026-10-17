;; This Source Code Form is subject to the terms of the Mozilla Public
;; License, v. 2.0. If a copy of the MPL was not distributed with this
;; file, You can obtain one at http://mozilla.org/MPL/2.0/.
;;
;; Copyright (c) KALEIDOS INC Sucursal en España SL

(ns frontend-tests.data.workspace-layout-default-test
  (:require
   [app.main.data.workspace.layout :as layout]
   [cljs.test :as t :include-macros true]))

(t/deftest default-layout-hides-rulers
  (t/testing "rulers are off by default"
    (t/is (not (contains? layout/default-layout :rulers)))))

(t/deftest default-layout-keeps-core-panels
  (t/testing "layers and sitemap stay on by default"
    (t/is (contains? layout/default-layout :layers))
    (t/is (contains? layout/default-layout :sitemap))))

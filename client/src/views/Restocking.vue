<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error && !hasLoaded" class="error">{{ error }}</div>
    <div v-else>
      <div v-if="orderResult" class="confirmation" role="status">
        <strong>{{ t('restocking.orderSuccess', { orderNumber: orderResult.order_number }) }}</strong>
        <span>{{ t('restocking.orderSuccessDetail', { date: formatDate(orderResult.expected_delivery) }) }}</span>
        <router-link to="/orders">{{ t('restocking.viewOrders') }}</router-link>
      </div>
      <div v-if="error" class="error">{{ error }}</div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.budget') }}</h3>
          <span class="budget-value">{{ formatMoney(budget) }}</span>
        </div>
        <div class="slider-wrap">
          <input
            type="range"
            :value="budget"
            class="budget-slider"
            min="0"
            :max="sliderMax"
            :step="sliderStep"
            :aria-label="t('restocking.budgetLabel')"
            @input="onBudgetInput($event.target.value)"
          />
          <div class="slider-scale">
            <span>{{ formatMoney(0) }}</span>
            <span>{{ t('restocking.maxBudget', { amount: formatMoney(maxBudget) }) }}</span>
          </div>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendations') }}</h3>
          <span v-if="refreshing" class="hint">{{ t('restocking.updating') }}</span>
        </div>

        <div v-if="items.length === 0" class="empty-state">{{ t('restocking.empty') }}</div>
        <template v-else>
          <div class="table-container">
            <table class="restocking-table">
              <thead>
                <tr>
                  <th class="col-check">
                    <input
                      type="checkbox"
                      :checked="allSelected"
                      :aria-label="t('restocking.selectAll')"
                      @change="toggleAll($event.target.checked)"
                    />
                  </th>
                  <th>{{ t('restocking.table.item') }}</th>
                  <th>{{ t('restocking.table.category') }}</th>
                  <th class="num">{{ t('restocking.table.shortfall') }}</th>
                  <th class="num">{{ t('restocking.table.unitCost') }}</th>
                  <th class="num">{{ t('restocking.table.lineCost') }}</th>
                  <th class="num">{{ t('restocking.table.leadTime') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in items" :key="item.sku">
                  <td class="col-check">
                    <input
                      type="checkbox"
                      :checked="selected[item.sku]"
                      :aria-label="t('restocking.selectItem', { name: translateProductName(item.name) })"
                      @change="selected[item.sku] = $event.target.checked"
                    />
                  </td>
                  <td>
                    <strong>{{ translateProductName(item.name) }}</strong>
                    <div class="sku">{{ item.sku }}</div>
                  </td>
                  <td>{{ item.category }}</td>
                  <td class="num">{{ item.shortfall.toLocaleString() }}</td>
                  <td class="num">{{ formatUnitCost(item.unit_cost) }}</td>
                  <td class="num"><strong>{{ formatMoney(item.line_cost) }}</strong></td>
                  <td class="num">{{ item.lead_time_days }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <div class="summary">
          <div class="summary-text">
            <div>
              <strong>{{ t('restocking.selectedTotal') }}: {{ formatMoney(selectedTotal) }}</strong>
              <span class="hint">{{ t('restocking.ofBudget', { amount: formatMoney(budget) }) }}</span>
            </div>
            <div class="hint">
              {{ t('restocking.itemsSelected', { selected: selectedItems.length, total: items.length }) }}
            </div>
          </div>
          <button
            type="button"
            class="place-order"
            :disabled="selectedItems.length === 0 || submitting || refreshing || debouncing"
            @click="placeOrder"
          >
            {{ submitting ? t('restocking.submitting') : t('restocking.placeOrder') }}
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script>
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

export default {
  name: 'Restocking',
  setup() {
    const { t, currentCurrency, currentLocale, translateProductName } = useI18n()

    const loading = ref(true)
    const refreshing = ref(false)
    const hasLoaded = ref(false)
    const error = ref(null)
    const submitting = ref(false)
    const orderResult = ref(null)
    // True between a slider move and its (debounced) fetch, while the list still reflects the old budget
    const debouncing = ref(false)

    const maxBudget = ref(0)
    const budget = ref(0)
    const items = ref([])
    // sku -> checked; reset to all-checked whenever recommendations change
    const selected = ref({})

    const DEBOUNCE_MS = 250
    let debounceTimer = null
    // Monotonic id so a slow older response can't overwrite a newer one
    let requestSeq = 0

    // Fixed $100 steps; fine granularity for a ~$33K range
    const sliderStep = 100
    // Round max up to a step multiple so the thumb can reach the true max budget
    const sliderMax = computed(() => Math.ceil(maxBudget.value / sliderStep) * sliderStep)

    const selectedItems = computed(() => items.value.filter(i => selected.value[i.sku]))
    const selectedTotal = computed(() => selectedItems.value.reduce((sum, i) => sum + i.line_cost, 0))
    const allSelected = computed(() => items.value.length > 0 && selectedItems.value.length === items.value.length)

    const formatMoney = (amount) => formatCurrency(amount, currentCurrency.value)

    // Unit costs are small and often fractional (e.g. 42.50), so keep cents; formatMoney would round to whole dollars
    const formatUnitCost = (amount) => formatCurrencyWithDecimals(amount, currentCurrency.value, 2)

    const formatDate = (dateString) => {
      const date = new Date(dateString)
      if (isNaN(date.getTime())) return dateString
      return date.toLocaleDateString(currentLocale.value === 'ja' ? 'ja-JP' : 'en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric'
      })
    }

    const applyRecommendations = (data) => {
      items.value = data.items
      selected.value = Object.fromEntries(data.items.map(i => [i.sku, true]))
    }

    // Fetch recommendations for the current budget (undefined = no cap, used on first load)
    const loadRecommendations = async (budgetValue) => {
      const seq = ++requestSeq
      try {
        refreshing.value = true
        const data = await api.getRestockingRecommendations(budgetValue)
        if (seq !== requestSeq) return // a newer request is in flight or done
        if (budgetValue === undefined) {
          maxBudget.value = data.max_budget
          budget.value = data.max_budget
        }
        applyRecommendations(data)
        error.value = null
        hasLoaded.value = true
      } catch (err) {
        if (seq !== requestSeq) return
        error.value = t('restocking.loadError') + ': ' + err.message
      } finally {
        if (seq === requestSeq) {
          refreshing.value = false
          loading.value = false
        }
      }
    }

    const onBudgetInput = (value) => {
      // Update display immediately; only the API call is debounced
      budget.value = Number(value)
      // The previous order's confirmation no longer describes what is on screen
      orderResult.value = null
      debouncing.value = true
      clearTimeout(debounceTimer)
      debounceTimer = setTimeout(() => {
        debouncing.value = false
        loadRecommendations(budget.value)
      }, DEBOUNCE_MS)
    }

    const toggleAll = (checked) => {
      selected.value = Object.fromEntries(items.value.map(i => [i.sku, checked]))
    }

    const placeOrder = async () => {
      if (selectedItems.value.length === 0 || submitting.value) return
      try {
        submitting.value = true
        error.value = null
        orderResult.value = null
        // Order quantity is the full shortfall for each checked item
        const payload = selectedItems.value.map(i => ({ sku: i.sku, quantity: i.shortfall }))
        orderResult.value = await api.placeRestockingOrder(payload)
        // Refresh at the current budget. The backend does not decrement quantity_on_hand when an
        // order is placed, so ordered items still appear as recommendations afterwards.
        await loadRecommendations(budget.value)
      } catch (err) {
        error.value = t('restocking.orderError') + ': ' + (err.response?.data?.detail || err.message)
      } finally {
        submitting.value = false
      }
    }

    onMounted(() => loadRecommendations())
    onBeforeUnmount(() => clearTimeout(debounceTimer))

    return {
      t,
      loading,
      refreshing,
      hasLoaded,
      error,
      submitting,
      debouncing,
      orderResult,
      maxBudget,
      budget,
      items,
      selected,
      sliderStep,
      sliderMax,
      selectedItems,
      selectedTotal,
      allSelected,
      formatMoney,
      formatUnitCost,
      formatDate,
      onBudgetInput,
      toggleAll,
      placeOrder,
      translateProductName
    }
  }
}
</script>

<style scoped>
.budget-value {
  font-size: 1.25rem;
  font-weight: 600;
  color: #0f172a;
}

.slider-wrap {
  padding: 0.5rem 1.5rem 1.25rem;
}

.budget-slider {
  width: 100%;
  height: 6px;
  appearance: none;
  -webkit-appearance: none;
  background: #e2e8f0;
  border-radius: 3px;
  outline: none;
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #0f172a;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.4);
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #0f172a;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.4);
}

.budget-slider:focus-visible {
  box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.4);
}

.slider-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  font-size: 0.813rem;
  color: #64748b;
}

.restocking-table {
  width: 100%;
}

.col-check {
  width: 40px;
}

.num {
  text-align: right;
}

.sku {
  font-size: 0.813rem;
  color: #64748b;
}

.hint {
  font-size: 0.813rem;
  color: #64748b;
  margin-left: 0.5rem;
}

.empty-state {
  padding: 2rem 1.5rem;
  text-align: center;
  color: #64748b;
}

.summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 1rem;
  padding: 1rem 1.5rem;
  border-top: 1px solid #e2e8f0;
  color: #0f172a;
}

.place-order {
  padding: 0.625rem 1.25rem;
  background: #0f172a;
  color: white;
  border: none;
  border-radius: 6px;
  font-weight: 500;
  cursor: pointer;
}

.place-order:hover:not(:disabled) {
  background: #1e293b;
}

.place-order:disabled {
  background: #94a3b8;
  cursor: not-allowed;
}

.confirmation {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 1rem;
  padding: 0.875rem 1.25rem;
  margin-bottom: 1rem;
  background: #f0fdf4;
  border: 1px solid #86efac;
  border-radius: 8px;
  color: #166534;
}

.confirmation a {
  color: #166534;
  font-weight: 600;
  text-decoration: underline;
}
</style>

// @ts-check

import { createApp, computed, ref, watch } from 'vue'
import { normalizeTagInput } from '../geo.js'

export const TagPicker = {
  name: 'TagPicker',
  props: {
    mode: {
      type: String,
      default: 'simple',
    },
    modelValue: {
      type: Array,
      default: () => [],
    },
    suggestions: {
      type: Array,
      default: () => [],
    },
    states: {
      type: Object,
      default: () => ({}),
    },
    placeholder: {
      type: String,
      default: 'add tag',
    },
    disabled: {
      type: Boolean,
      default: false,
    },
  },
  emits: ['update:modelValue', 'set-state'],
  setup(props, { emit }) {
    const inputValue = ref('')
    const dropdownOpen = ref(false)
    const highlightIndex = ref(-1)

    const inputNeedle = computed(() => normalizeTagInput(inputValue.value))

    const filteredSuggestions = computed(() => {
      const needle = inputNeedle.value.toLowerCase()
      if (!needle) return []
      return props.suggestions
        .filter((tag) => {
          if (props.mode === 'bulk') {
            if (tag in props.states) return false
          } else if (props.modelValue.includes(tag)) {
            return false
          }
          return tag.toLowerCase().includes(needle)
        })
        .slice(0, 12)
    })

    const canCreateTag = computed(() => {
      const tag = inputNeedle.value
      if (!tag) return false
      if (props.mode === 'bulk' && tag in props.states) return false
      if (props.mode === 'simple' && props.modelValue.includes(tag)) return false
      return !filteredSuggestions.value.some((entry) => entry.toLowerCase() === tag.toLowerCase())
    })

    const dropdownVisible = computed(
      () =>
        dropdownOpen.value &&
        !!inputNeedle.value &&
        (filteredSuggestions.value.length > 0 || canCreateTag.value),
    )

    const dropdownOptionCount = computed(
      () => filteredSuggestions.value.length + (canCreateTag.value ? 1 : 0),
    )

    const visibleChips = computed(() => {
      if (props.mode === 'simple') {
        return [...props.modelValue].sort((a, b) => a.localeCompare(b))
      }
      return Object.keys(props.states).sort((a, b) => a.localeCompare(b))
    })

    /** @param {string} tag */
    function chipState(tag) {
      if (props.mode === 'simple') return 'full'
      return props.states[tag] || 'none'
    }

    /** @param {string} tag */
    function removeTag(tag) {
      if (props.disabled || props.mode === 'bulk') return
      emit(
        'update:modelValue',
        props.modelValue.filter((entry) => entry !== tag),
      )
    }

    /** @param {string} tag */
    function onChipClick(tag) {
      if (props.disabled || props.mode !== 'bulk') return
      emit('set-state', tag, 'cycle')
    }

    /** @param {string} raw */
    function addTag(raw) {
      if (props.disabled) return
      const tag = normalizeTagInput(raw)
      if (!tag) return
      if (props.mode === 'bulk') {
        emit('set-state', tag, 'full')
      } else if (!props.modelValue.includes(tag)) {
        emit('update:modelValue', [...props.modelValue, tag])
      }
      inputValue.value = ''
      dropdownOpen.value = false
      highlightIndex.value = -1
    }

    /** @param {FocusEvent} ev */
    function onInputBlur(ev) {
      const related = /** @type {HTMLElement|null} */ (ev.relatedTarget)
      if (related?.closest?.('.tag-picker')) return
      window.setTimeout(() => {
        dropdownOpen.value = false
      }, 120)
    }

    function selectHighlightedOption() {
      const index = highlightIndex.value
      if (index >= 0 && index < filteredSuggestions.value.length) {
        addTag(filteredSuggestions.value[index])
        return
      }
      if (canCreateTag.value && index === filteredSuggestions.value.length) {
        addTag(inputNeedle.value)
        return
      }
      if (canCreateTag.value) {
        addTag(inputNeedle.value)
        return
      }
      if (index >= 0 && filteredSuggestions.value[index]) {
        addTag(filteredSuggestions.value[index])
      }
    }

    /** @param {KeyboardEvent} ev */
    function onInputKeydown(ev) {
      if (ev.key === 'Enter') {
        ev.preventDefault()
        selectHighlightedOption()
        return
      }
      if (ev.key === 'ArrowDown') {
        ev.preventDefault()
        if (!inputNeedle.value) return
        dropdownOpen.value = true
        const max = dropdownOptionCount.value - 1
        highlightIndex.value = Math.min(highlightIndex.value + 1, max)
        return
      }
      if (ev.key === 'ArrowUp') {
        ev.preventDefault()
        highlightIndex.value = Math.max(highlightIndex.value - 1, 0)
        return
      }
      if (ev.key === 'Escape') {
        dropdownOpen.value = false
        highlightIndex.value = -1
      }
    }

    watch(inputValue, () => {
      dropdownOpen.value = !!inputNeedle.value
      highlightIndex.value = -1
    })

    return {
      inputValue,
      dropdownOpen,
      highlightIndex,
      inputNeedle,
      filteredSuggestions,
      canCreateTag,
      dropdownVisible,
      dropdownOptionCount,
      selectHighlightedOption,
      visibleChips,
      chipState,
      removeTag,
      onChipClick,
      addTag,
      onInputBlur,
      onInputKeydown,
    }
  },
  template: `
    <div class="tag-picker" :class="{ 'tag-picker--bulk': mode === 'bulk' }">
      <div class="site-tags tag-picker__chips" role="group">
        <span
          v-for="tag in visibleChips"
          :key="tag"
          class="site-tag tag-picker__chip"
          :class="{
            'site-tag--toggle': mode === 'bulk',
            'is-selected': chipState(tag) === 'full',
            'is-partial': chipState(tag) === 'partial',
          }"
          :role="mode === 'bulk' ? 'button' : undefined"
          :tabindex="mode === 'bulk' && !disabled ? 0 : undefined"
          :aria-pressed="mode === 'bulk' ? (chipState(tag) === 'full' ? 'true' : chipState(tag) === 'partial' ? 'mixed' : 'false') : undefined"
          @click="onChipClick(tag)"
          @keydown.enter.prevent="onChipClick(tag)"
          @keydown.space.prevent="onChipClick(tag)"
        >
          <span v-if="chipState(tag) === 'partial'" class="site-tag__partial-icon" aria-hidden="true">◐</span>
          <span>{{ tag }}</span>
          <button
            v-if="mode !== 'bulk'"
            type="button"
            class="site-tag__remove"
            :disabled="disabled"
            :title="'Remove ' + tag"
            :aria-label="'Remove ' + tag"
            @click.stop="removeTag(tag)"
          >×</button>
        </span>
      </div>
      <div class="tag-picker__input-wrap">
        <input
          v-model="inputValue"
          type="text"
          class="tag-picker__input"
          :placeholder="placeholder"
          autocomplete="off"
          maxlength="32"
          aria-label="Add tag"
          :disabled="disabled"
          @focus="dropdownOpen = !!inputNeedle"
          @blur="onInputBlur"
          @keydown="onInputKeydown"
        >
        <ul v-if="dropdownVisible" class="tag-picker__dropdown" role="listbox">
          <li
            v-for="(tag, index) in filteredSuggestions"
            :key="tag"
            class="tag-picker__option"
            :class="{ 'tag-picker__option--active': index === highlightIndex }"
            role="option"
            @mousedown.prevent="addTag(tag)"
          >{{ tag }}</li>
          <li
            v-if="canCreateTag"
            class="tag-picker__option tag-picker__option--create"
            :class="{ 'tag-picker__option--active': highlightIndex === filteredSuggestions.length }"
            role="option"
            @mousedown.prevent="addTag(inputNeedle)"
          >+ create tag "{{ inputNeedle }}"</li>
        </ul>
      </div>
    </div>
  `,
}

/**
 * Mount TagPicker into a DOM container (modals).
 * @param {HTMLElement|null} container
 * @param {() => object} getProps
 * @param {{ onUpdate?: (tags: string[]) => void, onSetState?: (tag: string, state: string) => void }} handlers
 */
export function mountTagPicker(container, getProps, handlers = {}) {
  if (!container) return null
  container.innerHTML = ''
  const mountEl = document.createElement('div')
  mountEl.className = 'tag-picker-mount'
  container.appendChild(mountEl)

  const app = createApp({
    components: { TagPicker },
    setup() {
      const propsRef = computed(getProps)
      return { propsRef, handlers }
    },
    template: `
      <TagPicker
        v-bind="propsRef"
        @update:modelValue="handlers.onUpdate"
        @set-state="(tag, state) => handlers.onSetState?.(tag, state)"
      />
    `,
  })
  app.mount(mountEl)
  return { app, mountEl }
}

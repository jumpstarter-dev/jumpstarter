package service

func MatchLabels(candidate map[string]string, target map[string]string) int {
	count := 0
	for k, vt := range target {
		if vc, ok := candidate[k]; ok && vc == vt {
			count += 1
		} else {
			return -1
		}
	}
	return count
}
